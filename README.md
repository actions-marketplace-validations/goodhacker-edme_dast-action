# GoodHacker DAST for CI/CD

*[Leer en español](README.es.md)*

Runs a [GoodHacker](https://goodhacker.com.co) DAST scan against a registered web application or API, waits for it to finish, passes or fails the build based on your criteria, and attaches the results to the run.

Includes:
- a **GitHub Action**;
- **`ghdast.py`**, a single-file CLI that only uses the Python 3.8+ standard library, so it runs on any runner with `python3`;
- templates for **GitLab CI, Azure DevOps, Jenkins, TeamCity and Bamboo**.

> The GoodHacker user interface and the action's messages are in Spanish.

## Credentials

1. In GoodHacker, go to **Análisis de vulnerabilidades → API** and create an API client.
   - Store the secret: it is shown only once.
   - The client acts with your user's permissions. Launching scans requires that permission; a read-only user gets 403.
2. Add these secrets to your CI:
   - `GH_DAST_API_URL`: the API base URL, shown on that same page (it ends in `/dast/api/v1`).
   - `GH_DAST_CLIENT_ID`: the client ID.
   - `GH_DAST_CLIENT_SECRET`: the secret.

   Instead of an API client you can use `GH_DAST_USERNAME` and `GH_DAST_PASSWORD` (Basic authentication), unless your account uses MFA.

## GitHub Actions

```yaml
- uses: goodhacker-edme/dast-action@v1
  with:
    api-url: ${{ secrets.GH_DAST_API_URL }}
    client-id: ${{ secrets.GH_DAST_CLIENT_ID }}
    client-secret: ${{ secrets.GH_DAST_CLIENT_SECRET }}
    webapp-id: 12
    fail-on-severity: high
```

- Results are attached as the `ghdast-results` artifact.
- A summary is added to the job page.
- The `scan-id`, `scan-state` and `passed` outputs are available to later steps.
- Inputs mirror the CLI options; see [action.yml](action.yml) for the full list.

## Other platforms

| Platform | Template |
|---|---|
| GitLab CI | [integrations/gitlab/ghdast.gitlab-ci.yml](integrations/gitlab/ghdast.gitlab-ci.yml) |
| Azure DevOps (with work items) | [integrations/azure-devops/ghdast-scan.yml](integrations/azure-devops/ghdast-scan.yml) |
| Jenkins (pipeline and freestyle) | [integrations/jenkins](integrations/jenkins/README.md) |
| TeamCity | [integrations/teamcity/GhdastScan.xml](integrations/teamcity/GhdastScan.xml) |
| Bamboo | [integrations/bamboo/bamboo-specs.yaml](integrations/bamboo/bamboo-specs.yaml) |

The templates download the CLI from your GoodHacker instance (`…/dast/api/cli/ghdast.py`) and can verify its SHA-256. You can also use the `ghdast.py` in this repository.

## `ghdast scan`

```
python3 ghdast.py scan --webapp-id 12 --fail-on-severity high --max-count medium=10 \
    --exclude-detection GH-DAST-0040 --fail-on-scan-error
```

| Option | Effect |
|---|---|
| `--webapp-id N` / `--webapp-name "Name"` | Application to scan; it must already be registered. The name must match exactly. |
| `--scan-type vulnerability\|discovery` | Scan type. Default: `vulnerability`. |
| `--profile-id`, `--auth-record-id` | Override the application's defaults. |
| `--name`, `--name-template` | Scan name. See [Scan name](#scan-name). |
| `--no-wait` | Launch the scan and exit with code 0 without waiting for it to finish. |
| `--poll-interval S`, `--timeout S`, `--cancel-on-timeout` | Status polling, maximum wait, and cancelling the scan when the wait runs out. |
| `--fail-on-severity SEV` | Fail if there is any finding of that severity or higher. |
| `--max-count sev=N,…` | Fail if any severity exceeds N findings. |
| `--fail-on-detection ID,…` | Fail if any of those detections appears. |
| `--exclude-detection ID,…` | Those detections do not count towards any criterion. |
| `--fail-on-scan-error` | Also fail if the scan does not finish or finishes without results. Without this option, that case passes. |
| `--report pdf\|none` | Attach the scan's PDF report. Default: `pdf`. |
| `--output-dir DIR` | Results folder. Default: `ghdast-results`. |

Only findings **detected in this scan** count. Findings ignored in the platform (false positives or accepted risk) do not count.

### Results

`--output-dir` contains:
- `result.json`: the scan, counts per severity, the criteria applied, the reasons for failing and the list of findings;
- `report.pdf`: the scan report;
- `status.txt`: a one-line result.

Depending on the platform, also:
- **GitHub:** job summary and step outputs.
- **Azure DevOps:** `GHDAST_SCAN_ID` and `GHDAST_PASSED` variables.
- **TeamCity:** build status.

### Exit codes

| Code | Meaning |
|---|---|
| 0 | Passed. |
| 1 | Did not meet the criteria. |
| 2 | Scan error or timeout; only with `--fail-on-scan-error`. |
| 3 | Configuration or API error. |

### Scan name

By default it is generated as `job #build date`. GitHub Actions, GitLab CI, Azure DevOps, Jenkins, TeamCity and Bamboo are detected automatically.

`--name-template` accepts the variables `job`, `build`, `branch`, `commit`, `platform`, `webapp` and `timestamp`. For example: `--name-template "{webapp}-{branch}-{build}"`.

## Azure DevOps: work items

`ghdast azure-workitems` creates one work item (a Bug by default) for each finding in `result.json` at or above `--min-severity`.
- It does not create duplicates: before creating one, it looks for the `ghdast-finding-<id>` tag.
- It uses `System.AccessToken`.
- The template enables it with `createWorkItems: true`.

## Versions

- `@v1` receives fixes and backward-compatible improvements.
- `@v1.x.y` pins an exact version.

## License

[MIT](LICENSE)
