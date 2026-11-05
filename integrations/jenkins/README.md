# Jenkins

Hay dos formas de usar `ghdast` en Jenkins. Las dos necesitan `python3` en el agente.

## Pipeline: paso `ghdastScan`

1. **Instala la librería.** En *Manage Jenkins → System → Global Pipeline Libraries*, añade una librería llamada `ghdast` que apunte a este repositorio, con `integrations/jenkins` como ruta de la librería. Necesita la opción de recuperación *Modern SCM*.
2. **Crea la credencial.** En *Credentials*, crea una de tipo *Username with password* con el ID `ghdast-client`: el usuario es el client ID y la contraseña, el secreto.
3. **Usa el paso en el Jenkinsfile:**

```groovy
@Library('ghdast') _
pipeline {
  agent any
  stages {
    stage('DAST') {
      steps {
        ghdastScan(apiUrl: 'https://<api>/dast/api/v1', credentialsId: 'ghdast-client',
                   webappId: 12, args: '--fail-on-severity high --max-count medium=10')
      }
    }
  }
}
```

### Parámetros de `ghdastScan`

| Parámetro | Obligatorio | Qué es |
|---|---|---|
| `apiUrl` | Sí, si no existe la variable `GH_DAST_API_URL` | URL base de la API. |
| `credentialsId` | Sí | ID de la credencial del paso 2. |
| `webappId` o `webappName` | Uno de los dos | Aplicación que se escanea. |
| `args` | No | Criterios y opciones de `ghdast scan`. |
| `outputDir` | No | Carpeta de resultados. Por defecto, `ghdast-results`. |
| `unstableOnFail` | No | Marca la build como UNSTABLE en vez de fallarla. |
| `cliSha256` | No | SHA-256 esperado de la CLI descargada. |

## Freestyle

1. En *Build Environment*, activa *Use secret text(s) or file(s)* con un binding de tipo *Username and password (separated)*:
   - variable del usuario: `GH_DAST_CLIENT_ID`;
   - variable de la contraseña: `GH_DAST_CLIENT_SECRET`.
2. Añade un paso *Execute shell*:

```sh
export GH_DAST_API_URL=https://<api>/dast/api/v1
python3 -c "import urllib.request,os;u=os.environ['GH_DAST_API_URL'].rstrip('/').rsplit('/',1)[0]+'/cli/ghdast.py';urllib.request.urlretrieve(u,'ghdast.py')"
python3 ghdast.py scan --webapp-id 12 --fail-on-severity high
```

3. En *Post-build Actions*, añade *Archive the artifacts* con `ghdast-results/**` y marca *Do not fail build if archiving returns nothing*.

Si el código de salida no es 0, el paso *Execute shell* hace fallar la build.
