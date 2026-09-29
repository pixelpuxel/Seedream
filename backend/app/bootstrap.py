def main() -> None:
    raise SystemExit(
        "Der unsichere Umgebungsvariablen-Bootstrap wurde entfernt. "
        "Verwende einmalig POST /api/v1/setup/initialize mit X-Setup-Token."
    )


if __name__ == "__main__":
    main()
