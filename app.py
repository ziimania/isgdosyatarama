import streamlit as st
import os
import json
import zipfile
import tempfile
import time
import shutil
import google.generativeai as genai
from pdf2image import convert_from_path
from PIL import Image

def dosya_adi_duzenle(isim):
    yasakli_karakterler = '<>:"/\\|?*'
    for harf in yasakli_karakterler:
        isim = isim.replace(harf, '')
    return isim.strip().title()

def create_zip(source_dir, output_zip):
    with zipfile.ZipFile(output_zip, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for root, dirs, files in os.walk(source_dir):
            for file in files:
                file_path = os.path.join(root, file)
                arcname = os.path.relpath(file_path, source_dir)
                zipf.write(file_path, arcname)

st.set_page_config(page_title="İSG Belge Ayrıştırıcı", page_icon="📄", layout="centered")

st.title("📄 İSG Belge Ayrıştırıcı (Yapay Zeka Destekli)")

api_key = st.text_input("Gemini API Anahtarınızı Girin:", type="password")
kullanici_ipucu = st.text_area("Yapay Zeka İçin Kılavuz/İpucu (İsteğe Bağlı):", placeholder="Örn: Her kişinin 2 sayfa sınav, 4 sayfa talimatı vardır. İlk sayfada isim çıkar, sonrakilerde sadece imza olabilir.")
uploaded_file = st.file_uploader("Lütfen tarama yapılmış PDF dosyasını yükleyin", type="pdf")

if st.button("Ayrıştırmayı Başlat", type="primary"):
    if not api_key:
        st.error("Lütfen bir Gemini API anahtarı girin!")
    elif not uploaded_file:
        st.error("Lütfen işlenecek PDF dosyasını yükleyin!")
    else:
        try:
            genai.configure(api_key=api_key)
            
            # DİNAMİK MODEL BULUCU (Otomatik Keşif)
            uygun_model = None
            aktif_modeller = []
            
            for m in genai.list_models():
                if 'generateContent' in m.supported_generation_methods:
                    aktif_modeller.append(m.name)
                    if 'gemini-1.5-flash' in m.name:
                        uygun_model = m.name
                        break
            
            # Eğer tam isimle bulamazsa, listedeki ilk uygun 1.5 modelini seç
            if not uygun_model:
                for ad in aktif_modeller:
                    if '1.5' in ad or 'vision' in ad:
                        uygun_model = ad
                        break
                        
            if not uygun_model:
                st.error(f"Görsel işleyebilen bir model bulunamadı! Hesabınızdaki aktif modeller: {aktif_modeller}")
                st.stop()
                
            model = genai.GenerativeModel(uygun_model)
            st.toast(f"Başarılı: {uygun_model} modeline bağlanıldı!", icon="✅")
            
        except Exception as e:
            st.error(f"API Yapılandırma Hatası: {e}")
            st.stop()

        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = os.path.join(temp_dir, "yuklenen_dosya.pdf")
            orijinal_klasor = os.path.join(temp_dir, "Orijinal_Sayfalar")
            ayrilmis_klasor_yolu = os.path.join(temp_dir, "Ayrilmis_Dosyalar")
            zip_yolu = os.path.join(temp_dir, "ISG_Dosyalari.zip")
            
            os.makedirs(orijinal_klasor, exist_ok=True)
            os.makedirs(ayrilmis_klasor_yolu, exist_ok=True)

            with open(pdf_path, "wb") as f:
                f.write(uploaded_file.getbuffer())

            st.info("PDF dosyası okunuyor, RAM korunarak yüksek kalitede (300 DPI) diske kaydediliyor...")
            
            try:
                sayfa_yollari = convert_from_path(
                    pdf_path, 
                    dpi=300, 
                    output_folder=orijinal_klasor, 
                    fmt="tiff", 
                    paths_only=True
                )
            except Exception as e:
                st.error("PDF parçalanırken hata oluştu! Hata: " + str(e))
                st.stop()
                
            toplam_sayfa = len(sayfa_yollari)
            progress_bar = st.progress(0)
            status_text = st.empty()
            
            dosya_sayaclari = {}
            basarili_sayisi = 0
            
            aktif_kisi = "Bilinmeyen_Kisi"
            aktif_tc = "BilinmeyenTC"

            for i, sayfa_yolu in enumerate(sayfa_yollari):
                status_text.text(f"Sayfa {i+1} / {toplam_sayfa} analiz ediliyor...")
                
                with Image.open(sayfa_yolu) as img:
                    islem_gorseli = img.convert('RGB')
                
                prompt = f"""Bu görsel bir İş Sağlığı ve Güvenliği (İSG) belgesidir. 
{f"KULLANICI KILAVUZU: {kullanici_ipucu}" if kullanici_ipucu else ""}

Lütfen form üzerindeki kutucuklara el yazısı ile yazılmış bilgileri bul:
1. "ADI SOYADI:" başlığının yanındaki el yazısı ismi (Örn: Naime Kaya). Okunmuyorsa "Bilinmeyen Kisi" yaz.
2. "T.C. KİMLİK NO:" başlığının yanındaki 11 haneli el yazısı rakamı. Okunmuyorsa "BilinmeyenTC" yaz.
3. Belge Türünü ("Sinav" veya "Talimat") olarak belirle.

Yanıtını sadece aşağıdaki formatta, düz bir JSON olarak ver. Başka hiçbir açıklama ekleme:
{{"isim": "Ad Soyad", "tc": "12345678901", "tur": "Sinav veya Talimat"}}"""
                
                okunan_isim = "Bilinmeyen_Kisi"
                okunan_tc = "BilinmeyenTC"
                belge_turu = "Hata"
                
                max_deneme = 3
                for deneme in range(max_deneme):
                    try:
                        response = model.generate_content([prompt, islem_gorseli])
                        response_text = response.text.replace("```json", "").replace("```", "").strip()
                        veri = json.loads(response_text)
                        
                        okunan_isim = dosya_adi_duzenle(veri.get("isim", "Bilinmeyen_Kisi"))
                        okunan_tc = dosya_adi_duzenle(str(veri.get("tc", "BilinmeyenTC")))
                        belge_turu = veri.get("tur", "Bilinmeyen_Tur").lower()
                        break
                    except Exception as e:
                        if "429" in str(e) or "quota" in str(e).lower():
                            status_text.text(f"API sınırına ulaşıldı. Sayfa {i+1} için 10 sn bekleniyor... ({deneme+1}/{max_deneme})")
                            time.sleep(10)
                        else:
                            st.error(f"Sayfa {i+1} işlenirken hata oluştu: {str(e)}")
                            break
                            
                if okunan_isim != "Bilinmeyen_Kisi" and okunan_isim != "":
                    aktif_kisi = okunan_isim
                    if okunan_tc != "BilinmeyenTC" and okunan_tc != "":
                        aktif_tc = okunan_tc

                klasor_adi = f"{aktif_kisi}_{aktif_tc}"
                
                hedef_klasor = os.path.join(ayrilmis_klasor_yolu, klasor_adi)
                if not os.path.exists(hedef_klasor):
                    os.makedirs(hedef_klasor)

                if klasor_adi not in dosya_sayaclari:
                    dosya_sayaclari[klasor_adi] = {"sinav": 0, "talimat": 0, "diger": 0}
                
                if "sinav" in belge_turu:
                    dosya_sayaclari[klasor_adi]["sinav"] += 1
                    yeni_dosya_adi = f"Sinav_{dosya_sayaclari[klasor_adi]['sinav']}.tiff"
                elif "talimat" in belge_turu:
                    dosya_sayaclari[klasor_adi]["talimat"] += 1
                    yeni_dosya_adi = f"Talimat_{dosya_sayaclari[klasor_adi]['talimat']}.tiff"
                else:
                    dosya_sayaclari[klasor_adi]["diger"] += 1
                    yeni_dosya_adi = f"Tanimsiz_Belge_{dosya_sayaclari[klasor_adi]['diger']}.tiff"

                hedef_yol = os.path.join(hedef_klasor, yeni_dosya_adi)
                
                islem_gorseli.close()
                shutil.move(sayfa_yolu, hedef_yol)
                
                basarili_sayisi += 1
                progress_bar.progress((i + 1) / toplam_sayfa)
                
                time.sleep(4)

            status_text.text("Klasörler ZIP formatında sıkıştırılıyor...")
            create_zip(ayrilmis_klasor_yolu, zip_yolu)
            
            status_text.text("İşlem tamamlandı!")
            st.success(f"{basarili_sayisi} sayfa isme ve TC'ye göre arşivlendi.")
            
            with open(zip_yolu, "rb") as f:
                st.download_button(
                    label="📦 Hazırlanan Klasörleri İndir (ZIP)",
                    data=f,
                    file_name="ISG_Ayrilmis_Dosyalar.zip",
                    mime="application/zip",
                    type="primary"
                )
