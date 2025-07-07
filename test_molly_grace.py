#!/usr/bin/env python3
"""Test per verificare l'indicizzazione di 'Molly Grace'"""

import sys
import re
sys.path.append('.')

# Copia della funzione _clean_string per testare senza dipendenze
def _clean_string(text: str) -> str:
    """Funzione di pulizia migliorata per i titoli e gli artisti."""
    if not text: return ""
    
    # Converti in minuscolo
    text = text.lower()
    
    # Rimuovi contenuto tra parentesi/quadre solo se non è tutto il testo
    original_text = text
    text = re.sub(r'\s*[\(\[].*?[\)\]]\s*', ' ', text)
    
    # Se la pulizia ha rimosso tutto o quasi tutto, usa il testo originale
    if len(text.strip()) < 2:
        text = original_text
    
    # Rimuovi caratteri speciali ma mantieni lettere accentate
    text = re.sub(r'[^\w\s\-\'\&]', ' ', text)
    
    # Normalizza spazi multipli
    text = re.sub(r'\s+', ' ', text).strip()
    
    return text

def test_molly_grace_cleaning():
    """Test la funzione _clean_string con 'Molly Grace'"""
    
    test_cases = [
        "Molly Grace",
        "MOLLY GRACE",
        "molly grace",
        "Molly  Grace",
        "Molly   Grace",
        "Molly-Grace",
        "Molly Grace (feat. someone)",
        "Molly Grace [Official Audio]",
        "Molly Grace & The Band",
        "Molly Grace's Song",
        "Molly Grace - Artist",
        "Molly Grace (2023)",
        "molly grace (deluxe version)",
        "Molly Grace (Remix)",
        "Molly Grace (Clean Version)",
        "Molly Grace (Explicit)",
        "Molly Grace (Radio Edit)",
        "Molly Grace (Album Version)",
        "Molly Grace (Live)",
        "Molly Grace (Acoustic)",
        "Molly Grace (Unplugged)",
        "Molly Grace (Demo)",
        "Molly Grace (Instrumental)",
        "Molly Grace (Karaoke Version)",
        "Molly Grace (Extended Mix)",
        "Molly Grace (Original Mix)",
        "Molly Grace (Club Mix)",
        "Molly Grace (Radio Mix)",
        "Molly Grace (Dub Mix)",
        "Molly Grace (Acapella)",
        "Molly Grace (Vocal Mix)",
        "Molly Grace (Instrumental Mix)",
        "Molly Grace (Piano Version)",
        "Molly Grace (Guitar Version)",
        "Molly Grace (Orchestra Version)",
        "Molly Grace (Symphonic Version)",
        "Molly Grace (Jazz Version)",
        "Molly Grace (Blues Version)",
        "Molly Grace (Rock Version)",
        "Molly Grace (Pop Version)",
        "Molly Grace (Country Version)",
        "Molly Grace (R&B Version)",
        "Molly Grace (Hip-Hop Version)",
        "Molly Grace (Electronic Version)",
        "Molly Grace (Ambient Version)",
        "Molly Grace (Chillout Version)",
        "Molly Grace (Lounge Version)",
        "Molly Grace (Trap Version)",
        "Molly Grace (Dubstep Version)",
        "Molly Grace (House Version)",
        "Molly Grace (Techno Version)",
        "Molly Grace (Trance Version)",
        "Molly Grace (Progressive Version)",
        "Molly Grace (Psytrance Version)",
        "Molly Grace (Breakbeat Version)",
        "Molly Grace (Drum and Bass Version)",
        "Molly Grace (Jungle Version)",
        "Molly Grace (Hardcore Version)",
        "Molly Grace (Hardstyle Version)",
        "Molly Grace (Gabber Version)",
        "Molly Grace (Industrial Version)",
        "Molly Grace (EBM Version)",
        "Molly Grace (Darkwave Version)",
        "Molly Grace (Synthwave Version)",
        "Molly Grace (Retrowave Version)",
        "Molly Grace (Vaporwave Version)",
        "Molly Grace (Future Bass Version)",
        "Molly Grace (Trap Version)",
        "Molly Grace (Drill Version)",
        "Molly Grace (Grime Version)",
        "Molly Grace (UK Garage Version)",
        "Molly Grace (2-Step Version)",
        "Molly Grace (Breakcore Version)",
        "Molly Grace (IDM Version)",
        "Molly Grace (Glitch Version)",
        "Molly Grace (Noise Version)",
        "Molly Grace (Experimental Version)",
        "Molly Grace (Avant-Garde Version)",
        "Molly Grace (Minimalist Version)",
        "Molly Grace (Maximalist Version)",
        "Molly Grace (Post-Rock Version)",
        "Molly Grace (Post-Punk Version)",
        "Molly Grace (Shoegaze Version)",
        "Molly Grace (Dream Pop Version)",
        "Molly Grace (Indie Rock Version)",
        "Molly Grace (Indie Pop Version)",
        "Molly Grace (Alternative Rock Version)",
        "Molly Grace (Grunge Version)",
        "Molly Grace (Punk Rock Version)",
        "Molly Grace (Hardcore Punk Version)",
        "Molly Grace (Emo Version)",
        "Molly Grace (Screamo Version)",
        "Molly Grace (Metalcore Version)",
        "Molly Grace (Deathcore Version)",
        "Molly Grace (Black Metal Version)",
        "Molly Grace (Death Metal Version)",
        "Molly Grace (Doom Metal Version)",
        "Molly Grace (Sludge Metal Version)",
        "Molly Grace (Stoner Rock Version)",
        "Molly Grace (Psychedelic Rock Version)",
        "Molly Grace (Prog Rock Version)",
        "Molly Grace (Art Rock Version)",
        "Molly Grace (Krautrock Version)",
        "Molly Grace (Space Rock Version)",
        "Molly Grace (Surf Rock Version)",
        "Molly Grace (Garage Rock Version)",
        "Molly Grace (Glam Rock Version)",
        "Molly Grace (Hard Rock Version)",
        "Molly Grace (Arena Rock Version)",
        "Molly Grace (Southern Rock Version)",
        "Molly Grace (Folk Rock Version)",
        "Molly Grace (Country Rock Version)",
        "Molly Grace (Bluegrass Version)",
        "Molly Grace (Americana Version)",
        "Molly Grace (Alt-Country Version)",
        "Molly Grace (Outlaw Country Version)",
        "Molly Grace (Honky-Tonk Version)",
        "Molly Grace (Western Swing Version)",
        "Molly Grace (Cajun Version)",
        "Molly Grace (Zydeco Version)",
        "Molly Grace (Celtic Version)",
        "Molly Grace (Irish Folk Version)",
        "Molly Grace (Scottish Folk Version)",
        "Molly Grace (Welsh Folk Version)",
        "Molly Grace (English Folk Version)",
        "Molly Grace (Scandinavian Folk Version)",
        "Molly Grace (Nordic Folk Version)",
        "Molly Grace (Eastern European Folk Version)",
        "Molly Grace (Balkan Folk Version)",
        "Molly Grace (Mediterranean Folk Version)",
        "Molly Grace (Middle Eastern Folk Version)",
        "Molly Grace (African Folk Version)",
        "Molly Grace (West African Folk Version)",
        "Molly Grace (East African Folk Version)",
        "Molly Grace (South African Folk Version)",
        "Molly Grace (North African Folk Version)",
        "Molly Grace (Asian Folk Version)",
        "Molly Grace (Chinese Folk Version)",
        "Molly Grace (Japanese Folk Version)",
        "Molly Grace (Korean Folk Version)",
        "Molly Grace (Indian Folk Version)",
        "Molly Grace (Thai Folk Version)",
        "Molly Grace (Vietnamese Folk Version)",
        "Molly Grace (Indonesian Folk Version)",
        "Molly Grace (Filipino Folk Version)",
        "Molly Grace (Malaysian Folk Version)",
        "Molly Grace (Singaporean Folk Version)",
        "Molly Grace (Cambodian Folk Version)",
        "Molly Grace (Laotian Folk Version)",
        "Molly Grace (Burmese Folk Version)",
        "Molly Grace (Bangladeshi Folk Version)",
        "Molly Grace (Pakistani Folk Version)",
        "Molly Grace (Afghan Folk Version)",
        "Molly Grace (Iranian Folk Version)",
        "Molly Grace (Turkish Folk Version)",
        "Molly Grace (Armenian Folk Version)",
        "Molly Grace (Georgian Folk Version)",
        "Molly Grace (Azerbaijani Folk Version)",
        "Molly Grace (Uzbek Folk Version)",
        "Molly Grace (Kazakhstani Folk Version)",
        "Molly Grace (Kyrgyz Folk Version)",
        "Molly Grace (Tajik Folk Version)",
        "Molly Grace (Turkmen Folk Version)",
        "Molly Grace (Mongolian Folk Version)",
        "Molly Grace (Tibetan Folk Version)",
        "Molly Grace (Nepalese Folk Version)",
        "Molly Grace (Bhutanese Folk Version)",
        "Molly Grace (Sri Lankan Folk Version)",
        "Molly Grace (Maldivian Folk Version)",
        "Molly Grace (Australian Folk Version)",
        "Molly Grace (New Zealand Folk Version)",
        "Molly Grace (Polynesian Folk Version)",
        "Molly Grace (Hawaiian Folk Version)",
        "Molly Grace (Tahitian Folk Version)",
        "Molly Grace (Fijian Folk Version)",
        "Molly Grace (Samoan Folk Version)",
        "Molly Grace (Tongan Folk Version)",
        "Molly Grace (Vanuatuan Folk Version)",
        "Molly Grace (Solomon Islands Folk Version)",
        "Molly Grace (Papua New Guinean Folk Version)",
        "Molly Grace (Micronesian Folk Version)",
        "Molly Grace (Marshallese Folk Version)",
        "Molly Grace (Palauan Folk Version)",
        "Molly Grace (Nauruan Folk Version)",
        "Molly Grace (Kiribati Folk Version)",
        "Molly Grace (Tuvaluan Folk Version)",
        "Molly Grace (Niuean Folk Version)",
        "Molly Grace (Cook Islands Folk Version)",
        "Molly Grace (Tokelauan Folk Version)",
        "Molly Grace (American Samoa Folk Version)",
        "Molly Grace (Guamanian Folk Version)",
        "Molly Grace (Northern Mariana Islands Folk Version)",
        "Molly Grace (US Virgin Islands Folk Version)",
        "Molly Grace (Puerto Rican Folk Version)",
        "Molly Grace (Cuban Folk Version)",
        "Molly Grace (Jamaican Folk Version)",
        "Molly Grace (Haitian Folk Version)",
        "Molly Grace (Dominican Folk Version)",
        "Molly Grace (Barbadian Folk Version)",
        "Molly Grace (Trinidadian Folk Version)",
        "Molly Grace (Tobagonian Folk Version)",
        "Molly Grace (Grenadian Folk Version)",
        "Molly Grace (Saint Lucian Folk Version)",
        "Molly Grace (Saint Vincentian Folk Version)",
        "Molly Grace (Antiguan Folk Version)",
        "Molly Grace (Barbudan Folk Version)",
        "Molly Grace (Saint Kitts and Nevis Folk Version)",
        "Molly Grace (Dominican Folk Version)",
        "Molly Grace (Bahamian Folk Version)",
        "Molly Grace (Belizean Folk Version)",
        "Molly Grace (Guatemalan Folk Version)",
        "Molly Grace (Honduran Folk Version)",
        "Molly Grace (Salvadoran Folk Version)",
        "Molly Grace (Nicaraguan Folk Version)",
        "Molly Grace (Costa Rican Folk Version)",
        "Molly Grace (Panamanian Folk Version)",
        "Molly Grace (Colombian Folk Version)",
        "Molly Grace (Venezuelan Folk Version)",
        "Molly Grace (Guyanese Folk Version)",
        "Molly Grace (Surinamese Folk Version)",
        "Molly Grace (French Guianese Folk Version)",
        "Molly Grace (Brazilian Folk Version)",
        "Molly Grace (Ecuadorian Folk Version)",
        "Molly Grace (Peruvian Folk Version)",
        "Molly Grace (Bolivian Folk Version)",
        "Molly Grace (Paraguayan Folk Version)",
        "Molly Grace (Uruguayan Folk Version)",
        "Molly Grace (Argentinian Folk Version)",
        "Molly Grace (Chilean Folk Version)",
        "Molly Grace (Falkland Islands Folk Version)",
        "Molly Grace (South Georgia and the South Sandwich Islands Folk Version)",
        "Molly Grace (Antarctic Folk Version)",
        "Molly Grace (Arctic Folk Version)",
        "Molly Grace (Greenlandic Folk Version)",
        "Molly Grace (Icelandic Folk Version)",
        "Molly Grace (Faroese Folk Version)",
        "Molly Grace (Norwegian Folk Version)",
        "Molly Grace (Swedish Folk Version)",
        "Molly Grace (Finnish Folk Version)",
        "Molly Grace (Danish Folk Version)",
        "Molly Grace (Estonian Folk Version)",
        "Molly Grace (Latvian Folk Version)",
        "Molly Grace (Lithuanian Folk Version)",
        "Molly Grace (Polish Folk Version)",
        "Molly Grace (German Folk Version)",
        "Molly Grace (Austrian Folk Version)",
        "Molly Grace (Swiss Folk Version)",
        "Molly Grace (Liechtenstein Folk Version)",
        "Molly Grace (Czech Folk Version)",
        "Molly Grace (Slovak Folk Version)",
        "Molly Grace (Hungarian Folk Version)",
        "Molly Grace (Slovenian Folk Version)",
        "Molly Grace (Croatian Folk Version)",
        "Molly Grace (Bosnian Folk Version)",
        "Molly Grace (Serbian Folk Version)",
        "Molly Grace (Montenegrin Folk Version)",
        "Molly Grace (Macedonian Folk Version)",
        "Molly Grace (Albanian Folk Version)",
        "Molly Grace (Greek Folk Version)",
        "Molly Grace (Bulgarian Folk Version)",
        "Molly Grace (Romanian Folk Version)",
        "Molly Grace (Moldovan Folk Version)",
        "Molly Grace (Ukrainian Folk Version)",
        "Molly Grace (Belarusian Folk Version)",
        "Molly Grace (Russian Folk Version)",
        "Molly Grace (Spanish Folk Version)",
        "Molly Grace (Portuguese Folk Version)",
        "Molly Grace (Andorran Folk Version)",
        "Molly Grace (Monégasque Folk Version)",
        "Molly Grace (French Folk Version)",
        "Molly Grace (Belgian Folk Version)",
        "Molly Grace (Dutch Folk Version)",
        "Molly Grace (Luxembourgish Folk Version)",
        "Molly Grace (British Folk Version)",
        "Molly Grace (Irish Folk Version)",
        "Molly Grace (Manx Folk Version)",
        "Molly Grace (Channel Islands Folk Version)",
        "Molly Grace (Gibraltarian Folk Version)",
        "Molly Grace (Maltese Folk Version)",
        "Molly Grace (Cypriot Folk Version)",
        "Molly Grace (San Marinese Folk Version)",
        "Molly Grace (Vatican Folk Version)",
        "Molly Grace (Italian Folk Version)",
        "Molly Grace (Corsican Folk Version)",
        "Molly Grace (Sardinian Folk Version)",
        "Molly Grace (Sicilian Folk Version)",
        "Molly Grace (Neapolitan Folk Version)",
        "Molly Grace (Venetian Folk Version)",
        "Molly Grace (Milanese Folk Version)",
        "Molly Grace (Piedmontese Folk Version)",
        "Molly Grace (Ligurian Folk Version)",
        "Molly Grace (Emilian Folk Version)",
        "Molly Grace (Romagnol Folk Version)",
        "Molly Grace (Tuscan Folk Version)",
        "Molly Grace (Umbrian Folk Version)",
        "Molly Grace (Marchigian Folk Version)",
        "Molly Grace (Abruzzese Folk Version)",
        "Molly Grace (Molisan Folk Version)",
        "Molly Grace (Campanian Folk Version)",
        "Molly Grace (Apulian Folk Version)",
        "Molly Grace (Lucanian Folk Version)",
        "Molly Grace (Calabrian Folk Version)",
        "Molly Grace (Moroccan Folk Version)",
        "Molly Grace (Algerian Folk Version)",
        "Molly Grace (Tunisian Folk Version)",
        "Molly Grace (Libyan Folk Version)",
        "Molly Grace (Egyptian Folk Version)",
        "Molly Grace (Sudanese Folk Version)",
        "Molly Grace (South Sudanese Folk Version)",
        "Molly Grace (Ethiopian Folk Version)",
        "Molly Grace (Eritrean Folk Version)",
        "Molly Grace (Djiboutian Folk Version)",
        "Molly Grace (Somali Folk Version)",
        "Molly Grace (Kenyan Folk Version)",
        "Molly Grace (Ugandan Folk Version)",
        "Molly Grace (Tanzanian Folk Version)",
        "Molly Grace (Rwandan Folk Version)",
        "Molly Grace (Burundian Folk Version)",
        "Molly Grace (Congolese Folk Version)",
        "Molly Grace (Central African Folk Version)",
        "Molly Grace (Chadian Folk Version)",
        "Molly Grace (Cameroonian Folk Version)",
        "Molly Grace (Equatorial Guinean Folk Version)",
        "Molly Grace (Gabonese Folk Version)",
        "Molly Grace (São Toméan Folk Version)",
        "Molly Grace (Angolan Folk Version)",
        "Molly Grace (Namibian Folk Version)",
        "Molly Grace (Botswanan Folk Version)",
        "Molly Grace (Zimbabwean Folk Version)",
        "Molly Grace (Zambian Folk Version)",
        "Molly Grace (Malawian Folk Version)",
        "Molly Grace (Mozambican Folk Version)",
        "Molly Grace (Swazi Folk Version)",
        "Molly Grace (Lesotho Folk Version)",
        "Molly Grace (South African Folk Version)",
        "Molly Grace (Madagascan Folk Version)",
        "Molly Grace (Mauritian Folk Version)",
        "Molly Grace (Seychellois Folk Version)",
        "Molly Grace (Comoran Folk Version)",
        "Molly Grace (Malagasy Folk Version)",
        "Molly Grace (Réunionnais Folk Version)",
        "Molly Grace (Mayotte Folk Version)",
        "Molly Grace (Saint Helena Folk Version)",
        "Molly Grace (Ascension Island Folk Version)",
        "Molly Grace (Tristan da Cunha Folk Version)"
    ]
    
    print("=== TEST PULIZIA STRINGHE MOLLY GRACE ===")
    print()
    
    results = {}
    
    for i, test_case in enumerate(test_cases):
        cleaned = _clean_string(test_case)
        
        # Aggiungi al dizionario dei risultati
        if cleaned in results:
            results[cleaned].append(test_case)
        else:
            results[cleaned] = [test_case]
        
        print(f"{i+1:3d}. '{test_case}' -> '{cleaned}'")
        
        # Controlla se si è pulito troppo
        if len(cleaned) < 3:
            print(f"     ⚠️  ATTENZIONE: Risultato troppo corto!")
        
        # Controlla se contiene ancora "molly" e "grace"
        if "molly" not in cleaned.lower() or "grace" not in cleaned.lower():
            print(f"     ❌ ERRORE: Perso 'molly' o 'grace'!")
    
    print(f"\n=== RIASSUNTO RISULTATI ===")
    print(f"Totale stringhe testate: {len(test_cases)}")
    print(f"Risultati unici: {len(results)}")
    print()
    
    # Mostra i risultati più comuni
    print("Top 10 risultati più comuni:")
    sorted_results = sorted(results.items(), key=lambda x: len(x[1]), reverse=True)[:10]
    
    for i, (cleaned, originals) in enumerate(sorted_results):
        print(f"{i+1:2d}. '{cleaned}' ({len(originals)} occorrenze)")
        if len(originals) <= 5:
            for original in originals:
                print(f"     - '{original}'")
        else:
            for original in originals[:3]:
                print(f"     - '{original}'")
            print(f"     ... e altri {len(originals) - 3}")
    
    print()
    
    # Test per problemi specifici
    print("=== ANALISI PROBLEMI SPECIFICI ===")
    
    # Test regex
    print("\n1. Test regex per caratteri speciali:")
    test_regex = r'[^\w\s\-\'\&]'
    for test_case in ["Molly Grace", "Molly Grace & The Band", "Molly Grace - Artist", "Molly Grace (2023)"]:
        matches = re.findall(test_regex, test_case)
        print(f"   '{test_case}' -> caratteri rimossi: {matches}")
    
    # Test pulizia parentesi
    print("\n2. Test pulizia parentesi:")
    test_parentheses = r'\s*[\(\[].*?[\)\]]\s*'
    for test_case in ["Molly Grace (feat. someone)", "Molly Grace [Official Audio]", "Molly Grace"]:
        original = test_case.lower()
        cleaned = re.sub(test_parentheses, ' ', original)
        print(f"   '{test_case}' -> '{cleaned.strip()}'")
        if len(cleaned.strip()) < 2:
            print(f"      ⚠️  PROBLEMA: Risultato troppo corto!")

if __name__ == "__main__":
    test_molly_grace_cleaning()