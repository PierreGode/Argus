"""Single source of truth for the Argus daemon version.

Consumed by:
  - argus-daemon.py     (--version flag, tray About text)
  - argus-daemon.spec   (embedded Windows VSVersionInfo)
  - packaging/windows/build-msi.ps1 and CI (MSI ProductVersion)

Keep this a plain "major.minor.patch" string. CI may append a build number
(-> "x.y.z.<run_number>") when stamping the MSI so each build upgrades cleanly.
"""

__version__ = "1.0.0"
