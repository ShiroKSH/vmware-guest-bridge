[pscustomobject]@{
    Message = 'Hello from the guest'
    PowerShell = $PSVersionTable.PSVersion.ToString()
    Session = (Get-Process -Id $PID).SessionId
    Answer = 6 * 7
}
