#!/usr/bin/env python3
"""ghdast: escaneos DAST de GoodHacker desde pipelines de CI/CD (CICD-07..11, 13).

Un solo fichero, solo librería estándar (Python 3.8+), para que funcione en cualquier runner.

  ghdast scan            lanza el escaneo de una app ya registrada, espera (o no), evalúa los
                         criterios de la build y deja los resultados (JSON, PDF, resumen)
  ghdast azure-workitems crea elementos de trabajo de Azure DevOps a partir de un result.json

Configuración por variables de entorno:
  GH_DAST_API_URL        URL base de la API pública, p. ej. https://…/dast/api/v1
  GH_DAST_CLIENT_ID      cliente de API (recomendado) …
  GH_DAST_CLIENT_SECRET
  GH_DAST_USERNAME       … o usuario y contraseña (Basic)
  GH_DAST_PASSWORD

Códigos de salida: 0 pasa · 1 la build falla por los criterios · 2 error o timeout del escaneo
(solo con --fail-on-scan-error) · 3 error de configuración o de la API.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

__version__ = "1.0.1"

SEVERITIES = ["critical", "high", "medium", "low", "info"]
RUNNING = {"submitted", "running", "processing", "canceling"}
WITH_RESULTS = {"finished", "finished_time_limit", "finished_link_limit", "canceled_with_results"}
EXIT_PASS, EXIT_FAIL, EXIT_SCAN_ERROR, EXIT_CONFIG = 0, 1, 2, 3


class ConfigError(Exception):
    pass


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(f"HTTP {status}: {message}")
        self.status = status


def log(msg: str) -> None:
    print(f"[ghdast] {msg}", file=sys.stderr, flush=True)


# -- Criterios de la build (CICD-08) ---------------------------------------------------------

def parse_counts(values) -> dict:
    """["high=5", "critical=0,medium=10"] → {"high": 5, "critical": 0, "medium": 10}."""
    out = {}
    for chunk in values or []:
        for item in str(chunk).split(","):
            if not item.strip():
                continue
            sev, sep, n = item.partition("=")
            sev = sev.strip().lower()
            if not sep or sev not in SEVERITIES or not n.strip().isdigit():
                raise ConfigError(f"--max-count: usa severidad=N (p. ej. high=5), no «{item}»")
            out[sev] = int(n)
    return out


def parse_ids(values) -> set:
    return {i.strip().upper() for chunk in values or [] for i in str(chunk).split(",") if i.strip()}


def evaluate(findings: list, *, fail_on_severity=None, max_counts=None, fail_on_detections=(),
             exclude_detections=()) -> dict:
    """Aplica los criterios a los hallazgos detectados en el escaneo. Las detecciones excluidas
    no cuentan para ningún criterio."""
    excluded = {d.upper() for d in exclude_detections}
    considered = [f for f in findings if str(f.get("detection_id", "")).upper() not in excluded]
    counts = {s: 0 for s in SEVERITIES}
    for f in considered:
        counts[f.get("severity") if f.get("severity") in counts else "info"] += 1
    reasons = []
    if fail_on_severity:
        threshold = SEVERITIES.index(fail_on_severity)
        hits = [f for f in considered if f.get("severity") in SEVERITIES
                and SEVERITIES.index(f["severity"]) <= threshold]
        if hits:
            reasons.append(f"{len(hits)} hallazgo(s) de severidad {fail_on_severity} o superior")
    for sev, limit in (max_counts or {}).items():
        if counts[sev] > limit:
            reasons.append(f"{counts[sev]} hallazgo(s) {sev}: el máximo es {limit}")
    wanted = {d.upper() for d in fail_on_detections} - excluded
    present = sorted({str(f.get("detection_id", "")).upper() for f in considered} & wanted)
    if present:
        reasons.append("detecciones prohibidas presentes: " + ", ".join(present))
    return {"passed": not reasons, "reasons": reasons, "counts": counts,
            "considered": len(considered), "excluded": len(findings) - len(considered)}


# -- Nombre del escaneo (CICD-11) ------------------------------------------------------------

_CI_VARS = [  # (plataforma, trabajo, build, rama, commit)
    ("github", "GITHUB_WORKFLOW", "GITHUB_RUN_NUMBER", "GITHUB_REF_NAME", "GITHUB_SHA"),
    ("gitlab", "CI_JOB_NAME", "CI_PIPELINE_IID", "CI_COMMIT_REF_NAME", "CI_COMMIT_SHA"),
    ("azure", "BUILD_DEFINITIONNAME", "BUILD_BUILDNUMBER", "BUILD_SOURCEBRANCHNAME", "BUILD_SOURCEVERSION"),
    ("jenkins", "JOB_NAME", "BUILD_NUMBER", "BRANCH_NAME", "GIT_COMMIT"),
    ("teamcity", "TEAMCITY_BUILDCONF_NAME", "BUILD_NUMBER", "BRANCH_NAME", "BUILD_VCS_NUMBER"),
    ("bamboo", "bamboo_buildPlanName", "bamboo_buildNumber", "bamboo_planRepository_branchName",
     "bamboo_planRepository_revision"),
]


def ci_context(env=None) -> dict:
    env = os.environ if env is None else env
    for platform, job, build, branch, commit in _CI_VARS:
        if env.get(job):
            return {"platform": platform, "job": env.get(job, ""), "build": env.get(build, ""),
                    "branch": env.get(branch, ""), "commit": (env.get(commit) or "")[:8]}
    return {"platform": "local", "job": "ghdast", "build": "", "branch": "", "commit": ""}


def scan_name(template: str | None, explicit: str | None, webapp: str, env=None,
              now: dt.datetime | None = None) -> str:
    if explicit:
        return explicit
    ctx = ci_context(env)
    now = now or dt.datetime.now(dt.timezone.utc)
    values = {**ctx, "webapp": webapp, "timestamp": now.strftime("%Y%m%d-%H%M%S")}
    template = template or ("{job} #{build} {timestamp}" if ctx["build"] else "{job} {timestamp}")
    try:
        name = template.format(**values)
    except (KeyError, IndexError) as exc:
        raise ConfigError(f"--name-template: variable desconocida {exc}; usa job, build, branch, "
                          "commit, platform, webapp, timestamp") from None
    return " ".join(name.split())[:200] or webapp


# -- Cliente de la API pública ---------------------------------------------------------------

class Client:
    def __init__(self, base_url: str, *, client_id=None, client_secret=None, username=None,
                 password=None, timeout=60, sleep=time.sleep):
        if not base_url:
            raise ConfigError("falta GH_DAST_API_URL")
        self.base = base_url.rstrip("/")
        if not ((client_id and client_secret) or (username and password)):
            raise ConfigError("faltan credenciales: GH_DAST_CLIENT_ID/SECRET o GH_DAST_USERNAME/PASSWORD")
        self.client_id, self.client_secret = client_id, client_secret
        self.username, self.password = username, password
        self.timeout, self.sleep = timeout, sleep
        self._token, self._token_exp = None, 0.0

    @classmethod
    def from_env(cls, env=None, **kw):
        env = os.environ if env is None else env
        return cls(env.get("GH_DAST_API_URL", ""), client_id=env.get("GH_DAST_CLIENT_ID"),
                   client_secret=env.get("GH_DAST_CLIENT_SECRET"),
                   username=env.get("GH_DAST_USERNAME"), password=env.get("GH_DAST_PASSWORD"), **kw)

    def _auth_header(self) -> str:
        if self.client_id:
            if not self._token or time.time() > self._token_exp - 120:
                body = urllib.parse.urlencode({"grant_type": "client_credentials",
                                               "client_id": self.client_id,
                                               "client_secret": self.client_secret}).encode()
                tok = self._raw("POST", "/auth/token", body,
                                {"Content-Type": "application/x-www-form-urlencoded"})
                self._token = tok["access_token"]
                self._token_exp = time.time() + int(tok.get("expires_in", 3600))
            return "Bearer " + self._token
        raw = f"{self.username}:{self.password}".encode()
        return "Basic " + base64.b64encode(raw).decode()

    def _raw(self, method, path, data=None, headers=None, attempt=0):
        url = self.base + path
        req = urllib.request.Request(url, data=data, method=method, headers={
            "Accept": "application/json", "User-Agent": f"ghdast/{__version__}", **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                text = resp.read().decode("utf-8")
                return json.loads(text) if text else {}
        except urllib.error.HTTPError as e:
            text = e.read().decode("utf-8", "replace")
            if e.code == 429 and attempt < 8:
                wait = int(e.headers.get("Retry-After") or 5)
                log(f"límite de la API alcanzado; reintento en {wait} s")
                self.sleep(min(wait, 300))
                return self._raw(method, path, data, headers, attempt + 1)
            if e.code >= 500 and attempt < 3:
                self.sleep(2 ** attempt * 2)
                return self._raw(method, path, data, headers, attempt + 1)
            try:
                msg = json.loads(text).get("message") or json.loads(text).get("error") or text
            except (json.JSONDecodeError, AttributeError):
                msg = text[:300]
            raise ApiError(e.code, msg) from None
        except urllib.error.URLError as e:
            if attempt < 3:
                self.sleep(2 ** attempt * 2)
                return self._raw(method, path, data, headers, attempt + 1)
            raise ApiError(0, f"sin conexión con {url}: {e.reason}") from None

    def call(self, method, path, body=None, query=None):
        if query:
            path += "?" + urllib.parse.urlencode(query)
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Authorization": self._auth_header()}
        if data is not None:
            headers["Content-Type"] = "application/json"
        try:
            return self._raw(method, path, data, headers)
        except ApiError as e:
            if e.status == 401 and self.client_id and self._token:
                self._token = None  # token caducado o revocado: se pide otro una vez
                headers["Authorization"] = self._auth_header()
                return self._raw(method, path, data, headers)
            raise

    def all(self, path, key, query=None):
        out, offset = [], 0
        while True:
            page = self.call("GET", path, query={**(query or {}), "limit": 1000, "offset": offset})
            out += page.get(key) or []
            nxt = (page.get("pagination") or {}).get("next_offset")
            if nxt is None:
                return out
            offset = nxt


def download(url: str, dest: str, timeout=120) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": f"ghdast/{__version__}"})
    with urllib.request.urlopen(req, timeout=timeout) as resp, open(dest, "wb") as fh:
        fh.write(resp.read())


# -- Comando scan (CICD-07, 10) --------------------------------------------------------------

def resolve_webapp(client: Client, webapp_id, webapp_name) -> dict:
    if webapp_id:
        return client.call("GET", f"/webapps/{int(webapp_id)}")
    apps = client.call("GET", "/webapps", query={"filter.name": webapp_name})["webapps"]
    exact = [a for a in apps if a["name"] == webapp_name]
    if len(exact) != 1:
        raise ConfigError(f"se esperaba una app llamada «{webapp_name}» y hay {len(exact)}")
    return exact[0]


def wait_scan(client: Client, scan_id: int, *, poll: int, timeout: int, clock=time.time,
              sleep=time.sleep) -> tuple[dict, bool]:
    """Sondea hasta un estado final. Devuelve (escaneo, agotó_el_tiempo)."""
    deadline = clock() + timeout
    last = None
    while True:
        scan = client.call("GET", f"/scans/{scan_id}")
        if scan["state"] != last:
            log(f"escaneo {scan_id}: {scan['state']}")
            last = scan["state"]
        if scan["state"] not in RUNNING:
            return scan, False
        if clock() >= deadline:
            return scan, True
        sleep(max(5, min(poll, int(deadline - clock()) + 1)))


def fetch_report(client: Client, scan_id: int, dest: str, *, fmt="pdf", timeout=600,
                 clock=time.time, sleep=time.sleep) -> str | None:
    deadline = clock() + timeout
    while clock() < deadline:
        r = client.call("GET", f"/scans/{scan_id}/report", query={"format": fmt})
        if r.get("url"):
            download(r["url"], dest)
            return dest
        sleep(int(r.get("poll_after") or 5))
    log("el informe no estuvo listo a tiempo; se sigue sin él")
    return None


def _summary_md(result: dict) -> str:
    s, e = result["scan"], result.get("evaluation") or {}
    icon = "✅" if result["passed"] else "❌"
    lines = [f"## {icon} GoodHacker DAST · {s.get('name')}", "",
             f"Escaneo **#{s['id']}** de **{result['webapp']['name']}**: `{s['state']}`", ""]
    if e:
        lines += ["| " + " | ".join(SEVERITIES) + " |", "|" + "---|" * len(SEVERITIES),
                  "| " + " | ".join(str(e["counts"][k]) for k in SEVERITIES) + " |", ""]
    for r in result["reasons"]:
        lines.append(f"- {r}")
    return "\n".join(lines) + "\n"


def _write_outputs(result: dict, out_dir: str, env=None) -> None:
    env = os.environ if env is None else env
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "result.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2, ensure_ascii=False, default=str)
    with open(os.path.join(out_dir, "status.txt"), "w", encoding="utf-8") as fh:
        fh.write(f"{'PASSED' if result['passed'] else 'FAILED'} scan={result['scan']['id']} "
                 f"state={result['scan']['state']}\n")
    if env.get("GITHUB_STEP_SUMMARY"):
        with open(env["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as fh:
            fh.write(_summary_md(result))
    if env.get("GITHUB_OUTPUT"):
        with open(env["GITHUB_OUTPUT"], "a", encoding="utf-8") as fh:
            fh.write(f"scan_id={result['scan']['id']}\nscan_state={result['scan']['state']}\n"
                     f"passed={'true' if result['passed'] else 'false'}\nresult_json={path}\n"
                     f"report_pdf={result.get('report_pdf') or ''}\n")
    if env.get("TF_BUILD"):  # Azure DevOps: variables para pasos siguientes
        print(f"##vso[task.setvariable variable=GHDAST_SCAN_ID]{result['scan']['id']}")
        print(f"##vso[task.setvariable variable=GHDAST_PASSED]{str(result['passed']).lower()}")
    if env.get("TEAMCITY_VERSION"):
        status = "SUCCESS" if result["passed"] else "FAILURE"
        print(f"##teamcity[buildStatus status='{status}' text='GoodHacker DAST: {{build.status.text}}']")


def cmd_scan(args, client: Client, env=None, clock=time.time, sleep=time.sleep) -> int:
    env = os.environ if env is None else env
    criteria = {"fail_on_severity": args.fail_on_severity,
                "max_counts": parse_counts(args.max_count),
                "fail_on_detections": parse_ids(args.fail_on_detection),
                "exclude_detections": parse_ids(args.exclude_detection)}
    app = resolve_webapp(client, args.webapp_id, args.webapp_name)
    name = scan_name(args.name_template, args.name, app["name"], env)
    body = {"webapp_id": app["id"], "name": name, "scan_type": args.scan_type}
    for k in ("profile_id", "auth_record_id"):
        if getattr(args, k):
            body[k] = getattr(args, k)
    scan = client.call("POST", "/scans", body)
    log(f"escaneo {scan['id']} lanzado: «{name}» sobre {app['name']}")
    result = {"version": __version__, "webapp": {"id": app["id"], "name": app["name"]},
              "scan": {"id": scan["id"], "name": name, "state": scan["state"]},
              "criteria": {**criteria, "fail_on_detections": sorted(criteria["fail_on_detections"]),
                           "exclude_detections": sorted(criteria["exclude_detections"]),
                           "fail_on_scan_error": args.fail_on_scan_error},
              "ci": ci_context(env), "passed": True, "reasons": [], "findings": []}
    if args.no_wait:
        _write_outputs(result, args.output_dir, env)
        return EXIT_PASS
    scan, timed_out = wait_scan(client, scan["id"], poll=args.poll_interval, timeout=args.timeout,
                                clock=clock, sleep=sleep)
    result["scan"].update({k: scan.get(k) for k in ("state", "state_reason", "started_at",
                                                     "finished_at", "auth_status")})
    code = EXIT_PASS
    if timed_out:
        result["reasons"].append(f"el escaneo no terminó en {args.timeout} s")
        if args.cancel_on_timeout:
            client.call("POST", f"/scans/{scan['id']}/cancel", {})
            result["reasons"].append("escaneo cancelado")
        if args.fail_on_scan_error:
            code = EXIT_SCAN_ERROR
    elif scan["state"] not in WITH_RESULTS:
        result["reasons"].append(f"el escaneo terminó sin resultados: {scan['state']}"
                                 + (f" ({scan.get('state_reason')})" if scan.get("state_reason") else ""))
        if args.fail_on_scan_error:
            code = EXIT_SCAN_ERROR
    else:
        findings = [f for f in client.all("/findings", "findings", {"scan_id": scan["id"]})
                    if f.get("last_outcome") == "detected"]
        ev = evaluate(findings, **criteria)
        result["evaluation"] = ev
        result["reasons"] += ev["reasons"]
        result["findings"] = [{k: f.get(k) for k in ("id", "detection_id", "title", "severity",
                                                     "url", "method", "parameter", "state")}
                              for f in findings]
        if not ev["passed"]:
            code = EXIT_FAIL
        if args.report != "none":
            os.makedirs(args.output_dir, exist_ok=True)
            result["report_pdf"] = fetch_report(client, scan["id"],
                                                os.path.join(args.output_dir, "report.pdf"),
                                                clock=clock, sleep=sleep)
    result["passed"] = code == EXIT_PASS
    result["exit_code"] = code
    _write_outputs(result, args.output_dir, env)
    for r in result["reasons"]:
        log(r)
    log(("PASA" if result["passed"] else "FALLA") + f" (código {code}); resultados en {args.output_dir}")
    return code


# -- Comando azure-workitems (CICD-13) -------------------------------------------------------

def azure_workitems(result: dict, *, org_url: str, project: str, token: str, wi_type="Bug",
                    min_severity="high", area_path=None, opener=urllib.request.urlopen) -> dict:
    """Un elemento de trabajo por hallazgo detectado con severidad ≥ min_severity. No duplica:
    busca antes por la etiqueta `ghdast-finding-<id>`."""
    base = org_url.rstrip("/") + "/" + urllib.parse.quote(project)
    auth = "Basic " + base64.b64encode(f":{token}".encode()).decode()
    threshold = SEVERITIES.index(min_severity)
    created, existing = [], []

    def request(method, url, body, ctype):
        req = urllib.request.Request(url, data=json.dumps(body).encode(), method=method,
                                     headers={"Authorization": auth, "Content-Type": ctype,
                                              "Accept": "application/json"})
        with opener(req, timeout=60) as resp:
            return json.loads(resp.read().decode() or "{}")

    for f in result.get("findings") or []:
        if f.get("severity") not in SEVERITIES or SEVERITIES.index(f["severity"]) > threshold:
            continue
        tag = f"ghdast-finding-{f['id']}"
        wiql = {"query": "SELECT [System.Id] FROM WorkItems WHERE [System.TeamProject] = @project "
                         f"AND [System.Tags] CONTAINS '{tag}' AND [System.State] <> 'Removed'"}
        found = request("POST", f"{base}/_apis/wit/wiql?api-version=7.0", wiql, "application/json")
        if found.get("workItems"):
            existing.append(f["id"])
            continue
        title = f"[DAST {f['severity']}] {f.get('title') or f.get('detection_id')} · {f.get('url')}"[:250]
        desc = (f"<p>Detección {f.get('detection_id')} en <code>{f.get('method')} {f.get('url')}</code>"
                + (f", parámetro <code>{f.get('parameter')}</code>" if f.get("parameter") else "")
                + f".</p><p>Escaneo #{result['scan']['id']} de {result['webapp']['name']} "
                  f"(hallazgo {f['id']} en GoodHacker).</p>")
        ops = [{"op": "add", "path": "/fields/System.Title", "value": title},
               {"op": "add", "path": "/fields/System.Description", "value": desc},
               {"op": "add", "path": "/fields/System.Tags", "value": f"GoodHacker; DAST; {tag}"}]
        if wi_type == "Bug":
            ops.append({"op": "add", "path": "/fields/Microsoft.VSTS.TCM.ReproSteps", "value": desc})
        if area_path:
            ops.append({"op": "add", "path": "/fields/System.AreaPath", "value": area_path})
        wi = request("POST", f"{base}/_apis/wit/workitems/${urllib.parse.quote(wi_type)}"
                             "?api-version=7.0", ops, "application/json-patch+json")
        created.append({"finding_id": f["id"], "work_item_id": wi.get("id")})
    return {"created": created, "already_existing": existing}


# -- Entrada ---------------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ghdast", description="Escaneos DAST de GoodHacker en CI/CD")
    p.add_argument("--version", action="version", version=f"ghdast {__version__}")
    sub = p.add_subparsers(dest="command", required=True)
    s = sub.add_parser("scan", help="lanzar un escaneo y evaluar los criterios de la build")
    target = s.add_mutually_exclusive_group(required=True)
    target.add_argument("--webapp-id", type=int)
    target.add_argument("--webapp-name")
    s.add_argument("--scan-type", default="vulnerability", choices=["vulnerability", "discovery"])
    s.add_argument("--profile-id", type=int)
    s.add_argument("--auth-record-id", type=int)
    s.add_argument("--name", help="nombre del escaneo (por defecto, autogenerado)")
    s.add_argument("--name-template", help="p. ej. «{job}-{build}-{timestamp}»; variables: job, "
                   "build, branch, commit, platform, webapp, timestamp")
    s.add_argument("--no-wait", action="store_true", help="lanzar y salir sin esperar")
    s.add_argument("--poll-interval", type=int, default=30)
    s.add_argument("--timeout", type=int, default=4 * 3600, help="segundos (por defecto 4 h)")
    s.add_argument("--cancel-on-timeout", action="store_true")
    s.add_argument("--fail-on-severity", choices=SEVERITIES)
    s.add_argument("--max-count", action="append", help="severidad=N; la build falla si hay más")
    s.add_argument("--fail-on-detection", action="append", help="IDs de detección, separados por comas")
    s.add_argument("--exclude-detection", action="append", help="IDs que no cuentan para ningún criterio")
    s.add_argument("--fail-on-scan-error", action="store_true",
                   help="fallar si el escaneo no termina o termina sin resultados")
    s.add_argument("--report", default="pdf", choices=["pdf", "none"])
    s.add_argument("--output-dir", default="ghdast-results")
    w = sub.add_parser("azure-workitems", help="crear elementos de trabajo de Azure DevOps")
    w.add_argument("--result", default="ghdast-results/result.json")
    w.add_argument("--org-url", default=os.environ.get("SYSTEM_COLLECTIONURI"))
    w.add_argument("--project", default=os.environ.get("SYSTEM_TEAMPROJECT"))
    w.add_argument("--token", default=os.environ.get("SYSTEM_ACCESSTOKEN"))
    w.add_argument("--type", default="Bug")
    w.add_argument("--min-severity", default="high", choices=SEVERITIES)
    w.add_argument("--area-path")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "scan":
            return cmd_scan(args, Client.from_env())
        if not (args.org_url and args.project and args.token):
            raise ConfigError("faltan --org-url, --project o --token (System.AccessToken)")
        with open(args.result, encoding="utf-8") as fh:
            result = json.load(fh)
        out = azure_workitems(result, org_url=args.org_url, project=args.project,
                              token=args.token, wi_type=args.type, min_severity=args.min_severity,
                              area_path=args.area_path)
        log(f"elementos de trabajo creados: {len(out['created'])}; ya existían: "
            f"{len(out['already_existing'])}")
        print(json.dumps(out))
        return EXIT_PASS
    except ConfigError as e:
        log(f"configuración: {e}")
        return EXIT_CONFIG
    except ApiError as e:
        log(f"API: {e}")
        return EXIT_CONFIG
    except OSError as e:
        log(f"E/S: {e}")
        return EXIT_CONFIG


if __name__ == "__main__":
    sys.exit(main())
