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
            model = genai.GenerativeModel('gemini-1.5-flash')
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
                
                prompt = f"""
                Bu görsel bir İş Sağlığı ve Güvenliği (İSG) belgesidir. 
                {f"KULLANICI KILAVUZU: {kullanici_ipucu}" if kullanici_ipucu else ""}
                
                Lütfen form üzerindeki kutucuklara el yazısı ile yazılmış bilgileri bul:
                1. "ADI SOYADI:" başlığının yanındaki el yazısı ismi (Örn: Naime Kaya). Okunmuyorsa "Bilinmeyen Kisi" yaz.
                2. "T.C. KİMLİK NO:" başlığının yanındaki 11 haneli el yazısı rakamı. Okunmuyorsa "BilinmeyenTC" yaz.
                3. Belge Türünü ("Sinav" veya "Talimat") olarak belirle.
                
                Yanıtını sadece aşağıdaki formatta, düz bir JSON olarak ver. Başka hiçbir açıklama ekleme:
                {{"isim": "Ad Soyad", "tc": "12345678901", "tur": "Sinav veya Talimat"}}
                """
                
                okunan_isim = "Bilinmeyen_Kisi"
                okunan_tc = "BilinmeyenTC"
                belge_turu = "Hata"
                
                max_deneme = 3
                for deneme in range(max_deneme):
                    try:
                        response = model.generate_content([prompt, islem_gorseli])
                      response_text = response.text.replace("```json", "").replace("```", "").strip()
                    
