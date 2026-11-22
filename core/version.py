"""Tofu's version, and where new versions are published.

VERSION is bumped for each release (build_installer.bat stamps it into the
installer). UPDATE_REPO is the GitHub repository ("owner/name") whose Releases
carry the installers - Tofu checks it once a day and offers the new version.
Leave it empty to turn the check off for builds you don't publish; people can
also point it elsewhere with the "update_repo" setting.
"""

VERSION = "1.1.1"      # 1.1.1: home screen cards, 'can friends connect?', server Overview checklist
#        1.1.0: renamed from BaoBun Control Panel to Tofu Server Manager
UPDATE_REPO = ""
