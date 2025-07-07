#!/usr/bin/env python3
"""
Test per verificare la funzionalità di riallineamento database
"""
import requests
import time
import json

def test_realignment_api():
    """Test dell'API di riallineamento database"""
    print("🧪 Test API di riallineamento database...")
    
    base_url = "http://localhost:5000"
    
    # 1. Test status dell'app
    try:
        response = requests.get(f"{base_url}/api/status")
        if response.status_code == 200:
            print("✅ App Flask raggiungibile")
        else:
            print(f"⚠️ App status: {response.status_code}")
    except Exception as e:
        print(f"❌ Errore connessione Flask: {e}")
        return False
    
    # 2. Test endpoint riallineamento
    try:
        response = requests.post(f"{base_url}/api/database/realign")
        data = response.json()
        
        if response.status_code == 200 and data.get('success'):
            print("✅ API riallineamento funziona correttamente")
            print(f"📋 Messaggio: {data.get('message')}")
            return True
        elif response.status_code == 409:
            print("⚠️ Operazione già in corso - API funziona ma sistema occupato")
            return True
        else:
            print(f"❌ API riallineamento fallita: {data}")
            return False
            
    except Exception as e:
        print(f"❌ Errore test API riallineamento: {e}")
        return False

def test_watcher_integration():
    """Test integrazione con Music Watcher"""
    print("\n🧪 Test integrazione Music Watcher...")
    
    try:
        response = requests.get("http://localhost:5000/api/watcher/status")
        data = response.json()
        
        if data.get('success'):
            watcher = data.get('status', {})
            print(f"✅ Music Watcher attivo: {watcher.get('running')}")
            print(f"📁 Cartella monitorata: {watcher.get('music_path')}")
            print(f"⏰ Ultimo check: {watcher.get('last_check')}")
            return True
        else:
            print(f"❌ Music Watcher non disponibile: {data}")
            return False
            
    except Exception as e:
        print(f"❌ Errore test Music Watcher: {e}")
        return False

def test_ui_accessibility():
    """Test accessibilità interfaccia web"""
    print("\n🧪 Test accessibilità interfaccia web...")
    
    try:
        response = requests.get("http://localhost:5000/")
        
        if response.status_code == 200:
            html_content = response.text
            
            # Cerca elementi chiave dell'interfaccia
            key_elements = [
                "Riallinea Database con Plex",  # Pulsante in italiano
                "btn-realign",  # ID del pulsante
                "realignDatabase()",  # Funzione JavaScript
                "dashboard.actions.realign_database",  # Chiave traduzione
            ]
            
            found_elements = []
            for element in key_elements:
                if element in html_content:
                    found_elements.append(element)
            
            print(f"✅ Elementi UI trovati: {len(found_elements)}/{len(key_elements)}")
            if len(found_elements) >= 3:  # Almeno 3 su 4 dovrebbero essere presenti
                print("✅ Interfaccia web integrata correttamente")
                return True
            else:
                print(f"⚠️ Alcuni elementi UI mancanti: {set(key_elements) - set(found_elements)}")
                return False
                
        else:
            print(f"❌ Interfaccia web non accessibile: {response.status_code}")
            return False
            
    except Exception as e:
        print(f"❌ Errore test interfaccia: {e}")
        return False

def main():
    """Esegue tutti i test"""
    print("🎯 === TEST FUNZIONALITÀ RIALLINEAMENTO DATABASE ===\n")
    
    tests = [
        ("API Riallineamento", test_realignment_api),
        ("Integrazione Music Watcher", test_watcher_integration),
        ("Accessibilità UI", test_ui_accessibility),
    ]
    
    results = []
    
    for test_name, test_func in tests:
        print(f"{'='*50}")
        result = test_func()
        results.append((test_name, result))
        print(f"{'='*50}\n")
    
    # Riepilogo
    print("📊 RIEPILOGO RISULTATI:")
    passed = 0
    for test_name, result in results:
        status = "✅ PASSATO" if result else "❌ FALLITO"
        print(f"  {test_name}: {status}")
        if result:
            passed += 1
    
    print(f"\n🎯 RISULTATO FINALE: {passed}/{len(results)} test passati")
    
    if passed == len(results):
        print("🎉 Tutte le funzionalità di riallineamento database sono operative!")
        print("\n💡 Per usare la funzione:")
        print("   1. Vai su http://localhost:5000")
        print("   2. Cerca il pulsante 'Riallinea Database con Plex' nella sezione Manutenzione")
        print("   3. Clicca e conferma per avviare il riallineamento")
        print("   4. Monitora i progressi nella dashboard")
    else:
        print(f"⚠️ {len(results) - passed} test falliti. Controlla la configurazione.")

if __name__ == "__main__":
    main()