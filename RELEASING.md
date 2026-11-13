# Making a release

1. Make and test the changes (`streamlit run app.py`; regenerate the two
   example reports from a known pair and eyeball them against the previous
   release).
2. Bump `VERSION` (e.g. `1.1.0`) and add a section to `CHANGELOG.md`.
3. Commit and tag:
   ```bash
   git add -A
   git commit -m "Release 1.1.0"
   git tag v1.1.0
   git push && git push --tags
   ```
4. On GitHub → Releases → *Draft a new release* → choose tag `v1.1.0`, paste the
   changelog section, publish.

Users get the new version the next time they double-click the launcher: it
checks the latest GitHub release, downloads it if newer, replaces the program
files (never their outputs or `.venv`), reinstalls requirements if they
changed, and starts the app. The version in use is shown in the app sidebar
and in the report footer.

New users: download `Source code (zip)` from the latest release, unzip, and
double-click `run_app.command` (first run: see README for the one-time
Gatekeeper step).
