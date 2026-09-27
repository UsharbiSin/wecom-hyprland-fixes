' 将 Windows 文档请求异步交给 Wine 桌面处理器。
Set objArgs = WScript.Arguments
If objArgs.Count > 0 Then
    Set objShell = CreateObject("WScript.Shell")
    objShell.Run "winebrowser.exe """ & objArgs(0) & """", 0, False
End If
