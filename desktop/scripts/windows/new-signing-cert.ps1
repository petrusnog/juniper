<#
.SYNOPSIS
  Gera o certificado com que o "Desktop release" assina o instalador do app desktop (ADR-033).

.DESCRIPTION
  Cria um certificado de assinatura de código AUTOASSINADO (CN = "Juniper Desktop", 5 anos) e grava,
  na pasta $OutDir (padrão: ~\juniper-signing):
    juniper-signing.pfx             a chave privada + o certificado, protegidos pela senha que você escolher
    juniper-signing.pfx.base64.txt  o mesmo .pfx em base64, para colar no segredo WIN_CSC_LINK do GitHub
    juniper-signing.cer             só o certificado PÚBLICO (pode ser copiado para outros PCs)
  No fim, o certificado público é colocado como confiável NESTE PC (pasta "Autoridades de Certificação
  Raiz Confiáveis" do seu usuário): o Windows mostra uma janela de aviso e você confirma com "Sim".
  É isso que faz a assinatura do instalador ser "válida" para o app, que confere antes de atualizar.

  NUNCA coloque o .pfx nem o .base64.txt no repositório. Guarde a senha: sem ela não dá para assinar.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\desktop\scripts\windows\new-signing-cert.ps1
#>
param(
  [string]$Name = 'Juniper Desktop',
  [int]$Years = 5,
  [string]$OutDir = (Join-Path $HOME 'juniper-signing')
)

$ErrorActionPreference = 'Stop'
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

$password = Read-Host 'Escolha uma senha para o arquivo .pfx (guarde-a)' -AsSecureString
if ($password.Length -lt 8) { throw 'Use uma senha com pelo menos 8 caracteres.' }

$cert = New-SelfSignedCertificate `
  -Type CodeSigningCert `
  -Subject "CN=$Name" `
  -KeyAlgorithm RSA -KeyLength 3072 -HashAlgorithm SHA256 `
  -KeyUsage DigitalSignature -KeyExportPolicy Exportable `
  -CertStoreLocation 'Cert:\CurrentUser\My' `
  -NotAfter (Get-Date).AddYears($Years)

$pfx = Join-Path $OutDir 'juniper-signing.pfx'
$cer = Join-Path $OutDir 'juniper-signing.cer'
Export-PfxCertificate -Cert $cert -FilePath $pfx -Password $password | Out-Null
Export-Certificate -Cert $cert -FilePath $cer | Out-Null
[Convert]::ToBase64String([IO.File]::ReadAllBytes($pfx)) | Set-Content -NoNewline -Path "$pfx.base64.txt"

# A chave privada vive só no .pfx (e no segredo do GitHub): tira o certificado do repositório do usuário.
Remove-Item -Path "Cert:\CurrentUser\My\$($cert.Thumbprint)" -DeleteKey

# Confia no certificado público neste PC (o Windows vai pedir confirmação).
Import-Certificate -FilePath $cer -CertStoreLocation 'Cert:\CurrentUser\Root' | Out-Null

Write-Host ''
Write-Host "Pronto. Arquivos em: $OutDir" -ForegroundColor Green
Write-Host ''
Write-Host 'Agora, no GitHub (Settings > Secrets and variables > Actions):'
Write-Host '  Secrets   WIN_CSC_LINK          = conteúdo de juniper-signing.pfx.base64.txt'
Write-Host '            WIN_CSC_KEY_PASSWORD  = a senha que você escolheu'
Write-Host "  Variables JUNIPER_PUBLISHER     = $Name"
Write-Host ''
Write-Host "Impressão digital (para conferir): $($cert.Thumbprint)"
