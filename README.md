# 📄 İSG Belge Ayrıştırıcı (Yapay Zeka Destekli)

Toplu halde taratılmış İş Sağlığı ve Güvenliği belgelerini **Gemini 1.5 Flash** kullanarak okur, RAM'i koruyarak sayfaların klonlarıyla API sorgusu atar ve yüksek çözünürlüklü asıl TIFF dosyalarını isme göre otomatik olarak klasörler.

**Özellikler:**
* Kılavuz (Prompt) Modülü ile sayfa dizilimi tanımlama
* API Optimizasyonu (Grayscale & 1024px Thumbnail dublör sistemi)
* Hata toleransı (429 Request aşımında otomatik 10 saniye bekleme)
* RAM Koruması (Sayfaları önbellek yerine diskte `tempfile` üzerinde işleme)
* Streamlit Cloud uyumlu `packages.txt` altyapısı
* TC Kimlik numarası tespiti ve Aktif Kişi Hafızası (İsimsiz sayfaları doğru kişiye bağlama)
