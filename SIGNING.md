# Podpis cyfrowy instalatora Przeglądarki

Wersja 0.2.0 ma w workflow GitHub Actions obsługę podpisu Authenticode dla pliku EXE i instalatora Inno Setup.

## Ważne

Repozytorium nie zawiera certyfikatu. Bez prawidłowego certyfikatu od zaufanego urzędu certyfikacji pliki pozostają niepodpisane. Nie używamy samopodpisanego certyfikatu, ponieważ nie zapewnia on zaufania Windows i może zwiększać liczbę ostrzeżeń.

## Co jest potrzebne

1. Certyfikat do podpisywania kodu Windows (Authenticode), wraz z plikiem PFX i hasłem.
2. W repozytorium GitHub otworzyć Settings -> Secrets and variables -> Actions.
3. Dodać dwa sekrety:
   - WINDOWS_CERTIFICATE_BASE64 — zawartość pliku PFX zakodowana jako Base64.
   - WINDOWS_CERTIFICATE_PASSWORD — hasło do PFX.
4. Uruchomić workflow ponownie. Pipeline podpisze EXE i instalator, a następnie umieści status podpisu w artefakcie.

Pliku PFX ani hasła nie wolno umieszczać w repozytorium, kodzie, artefaktach ani publicznych logach.

## Sprawdzenie podpisu

Po pobraniu instalatora można sprawdzić: Właściwości pliku -> Podpisy cyfrowe. Dla dodatkowej kontroli PowerShell:
`Get-AuthenticodeSignature .\Przegladarka-Setup-Windows-v0.2.0.exe | Format-List Status,SignerCertificate`

Sam podpis potwierdza wydawcę i integralność pliku. Nie gwarantuje automatycznego zniknięcia wszystkich ostrzeżeń SmartScreen — reputacja nowego programu buduje się z czasem.
