$ErrorActionPreference = "Stop"
foreach ($taskName in @("AStock-Monitor-1445", "AStock-Monitor-Close")) {
    if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
        Write-Output "已移除 $taskName"
    }
}
