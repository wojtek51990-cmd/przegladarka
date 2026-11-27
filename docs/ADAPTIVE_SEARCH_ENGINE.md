# Silnik adaptacyjnego wyszukiwania sklepów — plan działania

## Cel

Robot ma wyszukiwać produkty w publicznych, pierwszostronnych wyszukiwarkach sklepów, również w sklepach autorskich i nieznanych wcześniej. Nie może wymagać rozpoznania marki sklepu, platformy e-commerce, nazwy klasy CSS ani typowego fragmentu adresu produktu.

Wynikiem wyszukiwarki sklepu jest źródło prawdy o tym, co sklep zwrócił. Robot odczytuje odpowiedź i nie dopisuje własnych ofert, nie rozstrzyga dostępności i nie odrzuca produktu z powodu braku ceny.

## Architektura

1. **Rozpoznanie wyszukiwarki**
   - Analiza pól formularzy, etykiet, placeholderów, przycisków i atrybutów dostępności.
   - Rozpoznawanie publicznych formularzy GET i POST, tylko gdy pole zapytania jest jednoznaczne.
   - Odrzucanie osadzonych wyszukiwarek zewnętrznych (np. Google Custom Search), nawet jeśli formularz znajduje się w HTML sklepu.
   - Tylko formularze samej domeny. Pola haseł, tokenów, sesji i dane konta są pomijane.
   - Rozpoznane platformy mogą dostarczać dodatkową wskazówkę, ale nie są warunkiem wyszukiwania.

2. **Wykonanie zapytania**
   - GET i jawnie rozpoznany POST realizowane przez klienta HTTP.
   - Jedno żądanie wyszukiwania na wykryty formularz; bez wysyłania formularzy konta, koszyka lub płatności.
   - Sprawdzanie robots.txt, ograniczanie częstotliwości, pozostawanie w obrębie domeny.
   - Gdy statyczny HTML nie zawiera wyszukiwarki lub wyników, pojedyncza próba przez osadzony Chromium/Playwright na domenę.

3. **Adaptacyjne rozpoznawanie kart**
   - Analiza powtarzalnych elementów DOM, podobieństwa rodzeństwa, obrazów, nagłówków, danych Schema.org, identyfikatorów produktu, lokalnych pól ceny i dostępności.
   - Brak zależności od ścieżek typu /produkt/ oraz klas typu product-card.
   - Karta musi mieć link tej samej domeny i kilka niezależnych przesłanek, że reprezentuje pojedynczy produkt.
   - Po rozpoznaniu karty jej tytuł musi pasować do co najmniej jednego wyróżniającego terminu zapytania; odrzucane są przypadkowe karty z katalogu, gdy sklep zignorował frazę.
   - Kontenery list, menu, artykuły i nawigacja nie są produktami.

4. **Odczyt danych**
   - Tytuł i adres są przypisane do konkretnego linku produktu.
   - Cena jest pobierana wyłącznie z pola wewnątrz tej samej karty; cena banera lub innego produktu nie może zostać przypisana.
   - Dostępność jest przepisywana wyłącznie z karty. Brak danych pozostaje pusty.
   - Liczba wyników zgłoszona przez sklep jest oddzielona od liczby kart odczytanych przez robota.

5. **Raport diagnostyczny**
   - Dla każdej domeny: wykryta metoda, adres formularza, status odpowiedzi, liczba zgłoszona przez sklep, liczba odczytanych kart, kompletność tytułów/linków/cen i przyczyna braku wyników.
   - 403, CAPTCHA, logowanie i odmowa dostępu są raportowane, a nie obchodzone.

## Kolejność działania robota

1. Pobierz stronę startową sklepu i sprawdź robots.txt.
2. Spróbuj rozpoznać publiczny formularz wyszukiwania.
3. Jeżeli jest formularz, wykonaj zapytanie do niego.
4. Jeżeli wyszukiwarka jest tworzona przez JavaScript, użyj jednorazowo osadzonego Chromium.
5. Przeanalizuj odpowiedź wyszukiwarki, nie stronę główną ani przypadkowe linki.
6. Zidentyfikuj powtarzalne karty i odczytaj dane z każdej osobno.
7. Zapisz wyniki oraz raport diagnostyczny.
8. Nie zastępuj nieodczytanych wyników ofertami znalezionymi na stronach kategorii lub produktu.

## Kryteria akceptacji

- Testy jednostkowe na kilku różnych, sztucznie zbudowanych strukturach DOM bez znanych klas i adresów.
- Test formularza GET, jawnego formularza POST, formularza obcej domeny i formularza logowania.
- Test sklepu z kartami bez ceny i dostępności.
- Test ceny banera 99 zł obok dwóch kart z różnymi cenami.
- Test linków do poradnika, kategorii i nawigacji, które nie mogą stać się produktami.
- Test wyników renderowanych przez JavaScript.
- Test na żywych sklepach: raport ma pokazać faktycznie odczytane karty, a nie tylko wykryte formularze lub liczbę wyników sklepu.
- Zielony test bez choć jednego poprawnie odczytanego produktu nie może być przedstawiany jako potwierdzenie skuteczności wyszukiwania.

## Ograniczenia

Nie istnieje uniwersalny sposób zagwarantowania odczytu każdej witryny. Sklep może blokować automatyczne żądania, wymagać konta, CAPTCHA albo stosować niestandardowy przepływ, którego nie da się bezpiecznie odtworzyć. Takie przypadki muszą mieć jawny status „nieodczytane”, a nie fałszywe wyniki.

## Status implementacji

Pierwsza wersja silnika adaptacyjnego jest dodana w `app/adaptive_search.py` i zintegrowana z crawlerem. Po audycie przebiegu Actions dodano odrzucanie formularzy z sygnaturą zewnętrznego Google Custom Search oraz filtrowanie kart po tytule względem charakterystycznych słów zapytania. Obejmuje rozpoznawanie publicznych formularzy GET/POST, heurystyczne rozpoznawanie kart DOM i opcjonalne renderowanie Chromium. Przed uznaniem projektu za gotowy wymagane są testy automatyczne oraz testy na żywych sklepach.
