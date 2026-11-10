<#
.SYNOPSIS
  Faz um OUTRO PC confiar no certificado de assinatura do app desktop (só o certificado público).

.DESCRIPTION
  Use em cada PC em que o app estiver instalado, se não foi lá que o certificado foi gerado.
  Copie o juniper-signing.cer (público; não tem chave privada) e rode este script apontando para ele.
  O Windows pede confirmação ("Sim") para adicionar à pasta de autoridades confiáveis do SEU usuário.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\trust-signing-cert.ps1 -Cer C:\Users\Usuario\juniper-signing\juniper-signing.cer
#>
param([Parameter(Mandatory = $true)][string]$Cer)

$ErrorActionPreference = 'Stop'
if (-not (Test-Path $Cer)) { throw "Arquivo não encontrado: $Cer" }
$cert = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2 $Cer
if ($cert.HasPrivateKey) { throw 'Este arquivo tem chave privada: use só o .cer público.' }
Import-Certificate -FilePath $Cer -CertStoreLocation 'Cert:\CurrentUser\Root' | Out-Null
Write-Host "Confio agora em: $($cert.Subject)  (impressão digital $($cert.Thumbprint))" -ForegroundColor Green
