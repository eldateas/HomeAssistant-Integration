# Wechsel zu prod
git checkout prod

# Lösche alle Dateien (außer .git)
git rm -rf .

# Kopiere den aktuellen Stand von main
git checkout main -- .

# Stage und Commit
git add .
git commit -m "Update prod snapshot from main $(date +%Y-%m-%d)"

# Zurück zu main
git checkout main