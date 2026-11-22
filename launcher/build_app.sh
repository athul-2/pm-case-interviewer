#!/bin/bash
# Builds "Case Interviewer.app" on the Desktop: double-click it to start the app.
#   bash launcher/build_app.sh
# macOS only. It's an AppleScript applet rather than a shell-script bundle: if the project lives in a protected
# folder (Desktop, Documents), macOS only asks for access on behalf of a real app (a bash bundle is silently denied).
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
APP="$HOME/Desktop/Case Interviewer.app"
rm -rf "$APP"
osacompile -o "$APP" -e "do shell script quoted form of \"$HERE/start.sh\""
cp "$HERE/icon.icns" "$APP/Contents/Resources/applet.icns"
/usr/libexec/PlistBuddy -c "Set :CFBundleName Case Interviewer" "$APP/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Add :CFBundleIdentifier string local.case-interviewer.launcher" "$APP/Contents/Info.plist" 2>/dev/null \
  || /usr/libexec/PlistBuddy -c "Set :CFBundleIdentifier local.case-interviewer.launcher" "$APP/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Add :NSDesktopFolderUsageDescription string 'Case Interviewer runs from the folder you cloned it into.'" "$APP/Contents/Info.plist" 2>/dev/null || true
codesign --force --deep -s - "$APP"   # re-sign after swapping the icon
touch "$APP"
echo "built $APP"
