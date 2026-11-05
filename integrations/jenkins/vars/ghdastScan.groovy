// Paso de Jenkins (librería compartida) para GoodHacker DAST (CICD-09).
//
// Instalación: añade este repositorio (carpeta integrations/jenkins) como Global Pipeline Library
// llamada "ghdast" y crea una credencial "Username with password" con el client id y el secreto.
//
// Uso en un Jenkinsfile:
//   @Library('ghdast') _
//   pipeline {
//     agent any
//     stages {
//       stage('DAST') {
//         steps {
//           ghdastScan(apiUrl: 'https://…/dast/api/v1', credentialsId: 'ghdast-client',
//                      webappId: 12, args: '--fail-on-severity high')
//         }
//       }
//     }
//   }
//
// El agente necesita python3. Devuelve el código de salida y marca la build como FAILURE si no
// pasa los criterios (UNSTABLE si se pide con unstableOnFail: true).
def call(Map cfg = [:]) {
    def apiUrl = cfg.apiUrl ?: env.GH_DAST_API_URL
    if (!apiUrl) { error('ghdastScan: falta apiUrl') }
    def outDir = cfg.outputDir ?: 'ghdast-results'
    def target = cfg.webappId ? "--webapp-id ${cfg.webappId as int}" : "--webapp-name '${cfg.webappName}'"
    def code = 3
    withCredentials([usernamePassword(credentialsId: cfg.credentialsId,
                                      usernameVariable: 'GH_DAST_CLIENT_ID',
                                      passwordVariable: 'GH_DAST_CLIENT_SECRET')]) {
        withEnv(["GH_DAST_API_URL=${apiUrl}"]) {
            sh '''python3 -c "import urllib.request,os;u=os.environ['GH_DAST_API_URL'].rstrip('/').rsplit('/',1)[0]+'/cli/ghdast.py';urllib.request.urlretrieve(u,'ghdast.py')"'''
            if (cfg.cliSha256) { sh "echo '${cfg.cliSha256}  ghdast.py' | sha256sum -c -" }
            code = sh(returnStatus: true,
                      script: "python3 ghdast.py scan ${target} ${cfg.args ?: ''} --output-dir '${outDir}'")
        }
    }
    archiveArtifacts artifacts: "${outDir}/**", allowEmptyArchive: true
    if (code != 0) {
        def msg = "GoodHacker DAST: la build no pasa (código ${code}); ver ${outDir}/result.json"
        if (cfg.unstableOnFail) { unstable(msg) } else { error(msg) }
    }
    return code
}
