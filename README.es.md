# GoodHacker DAST para CI/CD

*[Read in English](README.md)*

Lanza un escaneo DAST de [GoodHacker](https://goodhacker.com.co) sobre una aplicación registrada, espera a que termine, decide si la build pasa o falla según tus criterios y adjunta los resultados a la ejecución.

Incluye:
- una **GitHub Action**;
- **`ghdast.py`**, una CLI de un solo fichero que solo usa la librería estándar de Python 3.8 o posterior y funciona en cualquier runner con `python3`;
- plantillas para **GitLab CI, Azure DevOps, Jenkins, TeamCity y Bamboo**.

## Credenciales

1. En GoodHacker, ve a **Análisis de vulnerabilidades → API** y crea un cliente de API.
   - Guarda el secreto: solo se muestra una vez.
   - El cliente actúa con los permisos de tu usuario. Para lanzar escaneos necesitas ese permiso; un usuario de solo lectura recibe 403.
2. Configura estos secretos en tu CI:
   - `GH_DAST_API_URL`: URL base de la API, que aparece en esa misma página (termina en `/dast/api/v1`).
   - `GH_DAST_CLIENT_ID`: el client ID.
   - `GH_DAST_CLIENT_SECRET`: el secreto.

   En lugar del cliente de API puedes usar `GH_DAST_USERNAME` y `GH_DAST_PASSWORD` (autenticación Basic), salvo que tu cuenta tenga MFA.

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

- Los resultados se adjuntan como el artefacto `ghdast-results`.
- El resumen aparece en la página del job.
- Los outputs `scan-id`, `scan-state` y `passed` quedan disponibles para los pasos siguientes.
- Los inputs equivalen a las opciones de la CLI; están todos en [action.yml](action.yml).

## Otras plataformas

| Plataforma | Plantilla |
|---|---|
| GitLab CI | [integrations/gitlab/ghdast.gitlab-ci.yml](integrations/gitlab/ghdast.gitlab-ci.yml) |
| Azure DevOps (con elementos de trabajo) | [integrations/azure-devops/ghdast-scan.yml](integrations/azure-devops/ghdast-scan.yml) |
| Jenkins (pipeline y freestyle) | [integrations/jenkins](integrations/jenkins/README.md) |
| TeamCity | [integrations/teamcity/GhdastScan.xml](integrations/teamcity/GhdastScan.xml) |
| Bamboo | [integrations/bamboo/bamboo-specs.yaml](integrations/bamboo/bamboo-specs.yaml) |

Las plantillas descargan la CLI de tu instancia de GoodHacker (`…/dast/api/cli/ghdast.py`) y, si quieres, comprueban su SHA-256. También puedes usar el `ghdast.py` de este repositorio.

## `ghdast scan`

```
python3 ghdast.py scan --webapp-id 12 --fail-on-severity high --max-count medium=10 \
    --exclude-detection GH-DAST-0040 --fail-on-scan-error
```

| Opción | Efecto |
|---|---|
| `--webapp-id N` / `--webapp-name "Nombre"` | Aplicación que se escanea; debe estar registrada. Con el nombre, tiene que coincidir exactamente. |
| `--scan-type vulnerability\|discovery` | Tipo de escaneo. Por defecto, `vulnerability`. |
| `--profile-id`, `--auth-record-id` | Sustituyen los valores por defecto de la app. |
| `--name`, `--name-template` | Nombre del escaneo. Ver la sección [Nombre del escaneo](#nombre-del-escaneo). |
| `--no-wait` | Lanza el escaneo y sale con código 0, sin esperar a que termine. |
| `--poll-interval S`, `--timeout S`, `--cancel-on-timeout` | Sondeo del estado, espera máxima y cancelación del escaneo si se agota. |
| `--fail-on-severity SEV` | Falla si hay algún hallazgo de esa severidad o superior. |
| `--max-count sev=N,…` | Falla si alguna severidad supera N hallazgos. |
| `--fail-on-detection ID,…` | Falla si aparece alguna de esas detecciones. |
| `--exclude-detection ID,…` | Esas detecciones no cuentan para ningún criterio. |
| `--fail-on-scan-error` | Falla también si el escaneo no termina o termina sin resultados. Sin esta opción, ese caso pasa. |
| `--report pdf\|none` | Adjunta el informe PDF del escaneo. Por defecto, `pdf`. |
| `--output-dir DIR` | Carpeta de resultados. Por defecto, `ghdast-results`. |

Solo cuentan los hallazgos **detectados en este escaneo**. Los ignorados en la plataforma (falsos positivos o riesgo aceptado) no cuentan.

### Resultados

En `--output-dir` se escriben:
- `result.json`: el escaneo, los conteos por severidad, los criterios aplicados, los motivos del fallo y la lista de hallazgos;
- `report.pdf`: el informe del escaneo;
- `status.txt`: una línea con el resultado.

Además, según la plataforma:
- **GitHub:** resumen del job y outputs del paso.
- **Azure DevOps:** variables `GHDAST_SCAN_ID` y `GHDAST_PASSED`.
- **TeamCity:** estado de la build.

### Códigos de salida

| Código | Significado |
|---|---|
| 0 | Pasa. |
| 1 | No pasa los criterios. |
| 2 | Error o timeout del escaneo; solo con `--fail-on-scan-error`. |
| 3 | Error de configuración o de la API. |

### Nombre del escaneo

Por defecto se genera como `trabajo #build fecha`. Se detectan automáticamente GitHub Actions, GitLab CI, Azure DevOps, Jenkins, TeamCity y Bamboo.

Con `--name-template` puedes usar las variables `job`, `build`, `branch`, `commit`, `platform`, `webapp` y `timestamp`. Por ejemplo: `--name-template "{webapp}-{branch}-{build}"`.

## Azure DevOps: elementos de trabajo

`ghdast azure-workitems` crea un elemento de trabajo (por defecto, de tipo Bug) por cada hallazgo de severidad `--min-severity` o superior del `result.json`.
- No crea duplicados: antes de crear uno, busca la etiqueta `ghdast-finding-<id>`.
- Usa `System.AccessToken`.
- La plantilla lo activa con `createWorkItems: true`.

## Versiones

- `@v1` recibe las correcciones y mejoras compatibles.
- `@v1.x.y` fija una versión exacta.

## Licencia

[MIT](LICENSE)
