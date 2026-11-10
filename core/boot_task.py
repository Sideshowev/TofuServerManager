"""Start the BaoBun agent when Windows boots - before anyone signs in.

"Run at startup" (the registry Run key) only starts BaoBun when someone signs in,
so after a nightly restart nothing would come back on a server PC nobody logs into.
This adds a Windows Task Scheduler task instead:

* trigger: at system start (1 minute delay so the network is up)
* runs as the signed-in Windows user, "whether the user is logged on or not",
  without storing their password (S4U). Servers keep using that user's folders
  (AppData, Documents, Steam). Limitation of S4U: no access to network shares
  (\\\\PC\\share) - servers whose folder is on another PC need a mapped drive or
  a local copy. Checkup warns about those.
* normal priority (Task Scheduler's default would run the games below normal),
  no 3-day time limit, restarts itself if it exits with an error

Creating or removing it needs admin rights: one Windows UAC prompt.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from typing import List, Optional
from xml.sax.saxutils import escape

TASK_NAME = "BaoBun Agent"


def current_user() -> str:
    dom = os.environ.get("USERDOMAIN") or ""
    user = os.environ.get("USERNAME") or ""
    return (dom + "\\" + user) if dom and user else user


def task_xml(command: str, arguments: str, workdir: str, user: Optional[str] = None, logon: str = "S4U") -> str:
    user = user or current_user()
    logon = "Password" if logon == "Password" else "S4U"
    return """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Author>BaoBun Control Panel</Author>
    <Description>Starts the BaoBun agent when the PC turns on, so game servers come back after a restart without anyone signing in.</Description>
  </RegistrationInfo>
  <Triggers>
    <BootTrigger><Enabled>true</Enabled><Delay>PT1M</Delay></BootTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>%(user)s</UserId>
      <LogonType>%(logon)s</LogonType>
      <RunLevel>HighestAvailable</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings><StopOnIdleEnd>false</StopOnIdleEnd><RestartOnIdle>false</RestartOnIdle></IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>4</Priority>
    <RestartOnFailure><Interval>PT1M</Interval><Count>5</Count></RestartOnFailure>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>%(cmd)s</Command>
      <Arguments>%(args)s</Arguments>
      <WorkingDirectory>%(wd)s</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
""" % {"user": escape(user), "cmd": escape(command), "args": escape(arguments), "wd": escape(workdir),
       "logon": logon}


def _quote(a: str) -> str:
    return '"%s"' % a if (" " in a and not a.startswith('"')) else a


def write_xml(parts: List[str], workdir: str, folder: Optional[str] = None, logon: str = "S4U") -> str:
    """Write the task definition (UTF-16, as schtasks wants) and return its path."""
    xml = task_xml(parts[0], " ".join(_quote(a) for a in parts[1:]), workdir, logon=logon)
    fd, path = tempfile.mkstemp(prefix="baobun_task_", suffix=".xml", dir=folder)
    with os.fdopen(fd, "wb") as f:
        f.write(xml.encode("utf-16"))
    return path


def install_commands(xml_path: str) -> List[str]:
    return ['schtasks /Create /F /TN "%s" /XML "%s"' % (TASK_NAME, xml_path),
            'del "%s"' % xml_path]


def remove_commands() -> List[str]:
    return ['schtasks /Delete /F /TN "%s"' % TASK_NAME]


def installed() -> Optional[bool]:
    """True/False on Windows (no admin needed), None elsewhere."""
    if os.name != "nt":
        return None
    try:
        r = subprocess.run(["schtasks", "/Query", "/TN", TASK_NAME], capture_output=True, text=True,
                           timeout=15, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return r.returncode == 0
    except Exception:
        return None


def install(parts: List[str], workdir: str, folder: Optional[str] = None) -> bool:
    """Create (or replace) the task. One UAC prompt; returns False if declined."""
    from core.hosting import run_elevated
    if folder:
        os.makedirs(folder, exist_ok=True)
    return run_elevated(install_commands(write_xml(parts, workdir, folder)))


def mode() -> Optional[str]:
    """"password" (can reach network shares), "s4u", or None when not installed / unknown."""
    if os.name != "nt":
        return None
    try:
        r = subprocess.run(["schtasks", "/Query", "/TN", TASK_NAME, "/XML"], capture_output=True, text=True,
                           timeout=15, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception:
        return None
    if r.returncode != 0:
        return None
    return "password" if "<LogonType>Password</LogonType>" in (r.stdout or "") else "s4u"


def install_with_password(parts: List[str], workdir: str, folder: Optional[str] = None) -> bool:
    """Like install(), but the task keeps the user's Windows password (typed into a
    Windows console prompt, never into BaoBun) so BaoBun can reach network shares -
    e.g. backups copied to another PC. Opens one visible admin window."""
    if os.name != "nt":
        return False
    import ctypes
    if folder:
        os.makedirs(folder, exist_ok=True)
    xml = write_xml(parts, workdir, folder, logon="Password")
    fd, script = tempfile.mkstemp(prefix="baobun_task_", suffix=".cmd", dir=folder)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("@echo off\r\ntitle BaoBun - start at boot\r\n")
        f.write("echo BaoBun needs your Windows password once so it can start at boot AND reach network\r\n")
        f.write("echo folders (like backups on another PC). Windows stores it - BaoBun never sees it.\r\n")
        f.write("echo.\r\n")
        f.write('schtasks /Create /F /TN "%s" /XML "%s" /RU "%s" /RP *\r\n' % (TASK_NAME, xml, current_user()))
        f.write("echo.\r\nif errorlevel 1 (echo Didn't work - check the password and try again.) else "
                "(echo Done - you can close this window.)\r\n")
        f.write('del "%s" >nul 2>&1\r\npause\r\ndel "%%~f0"\r\n' % xml)
    rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", "cmd.exe", '/c "%s"' % script, None, 1)
    return rc > 32


def remove() -> bool:
    from core.hosting import run_elevated
    return run_elevated(remove_commands())
