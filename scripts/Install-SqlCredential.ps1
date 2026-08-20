[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = "Medium")]
param(
    [ValidateNotNullOrEmpty()]
    [string]$Target = "ESQ_LEFTOVER_SQL",

    [ValidateNotNullOrEmpty()]
    [string]$UserName = "longtat"
)

$ErrorActionPreference = "Stop"

if (-not ("TestCoiCredential.NativeMethods" -as [type])) {
    Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;

namespace TestCoiCredential
{
    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    public struct NativeCredential
    {
        public UInt32 Flags;
        public UInt32 Type;
        [MarshalAs(UnmanagedType.LPWStr)] public string TargetName;
        [MarshalAs(UnmanagedType.LPWStr)] public string Comment;
        public System.Runtime.InteropServices.ComTypes.FILETIME LastWritten;
        public UInt32 CredentialBlobSize;
        public IntPtr CredentialBlob;
        public UInt32 Persist;
        public UInt32 AttributeCount;
        public IntPtr Attributes;
        [MarshalAs(UnmanagedType.LPWStr)] public string TargetAlias;
        [MarshalAs(UnmanagedType.LPWStr)] public string UserName;
    }

    public static class NativeMethods
    {
        [DllImport("advapi32.dll", EntryPoint = "CredWriteW", CharSet = CharSet.Unicode, SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        public static extern bool CredWrite(ref NativeCredential credential, UInt32 flags);
    }
}
"@
}

Write-Host "Store the read-only SQL login in Windows Credential Manager."
$enteredUser = Read-Host "SQL user [$UserName]"
if (-not [string]::IsNullOrWhiteSpace($enteredUser)) {
    $UserName = $enteredUser.Trim()
}
if ([string]::IsNullOrWhiteSpace($UserName)) {
    throw "SQL user cannot be empty."
}

$securePassword = Read-Host "SQL password" -AsSecureString
if ($securePassword.Length -eq 0) {
    throw "SQL password cannot be empty."
}
if (($securePassword.Length * 2) -gt 512) {
    throw "Credential Manager passwords cannot exceed 256 Unicode characters."
}
if (-not $PSCmdlet.ShouldProcess("Windows Credential Manager target '$Target'", "Create or replace SQL credential for user '$UserName'")) {
    return
}

$passwordPointer = [Runtime.InteropServices.Marshal]::SecureStringToCoTaskMemUnicode($securePassword)
try {
    $credential = New-Object TestCoiCredential.NativeCredential
    $credential.Flags = 0
    $credential.Type = 1
    $credential.TargetName = $Target
    $credential.Comment = "TEST COI SQL connection"
    $credential.CredentialBlobSize = [uint32]($securePassword.Length * 2)
    $credential.CredentialBlob = $passwordPointer
    $credential.Persist = 2
    $credential.AttributeCount = 0
    $credential.Attributes = [IntPtr]::Zero
    $credential.TargetAlias = $null
    $credential.UserName = $UserName

    if (-not [TestCoiCredential.NativeMethods]::CredWrite([ref]$credential, 0)) {
        $errorCode = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
        throw [ComponentModel.Win32Exception]::new($errorCode)
    }
    Write-Host "Credential '$Target' was stored successfully."
}
finally {
    if ($passwordPointer -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeCoTaskMemUnicode($passwordPointer)
    }
    $securePassword.Dispose()
}
