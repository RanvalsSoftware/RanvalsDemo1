# RanvalsDemo1

Ranvals Software Odoo uygulamaları deposu. DocuCraft sürümleri Odoo'nun major
sürüm kuralına uygun olarak `17.0`, `18.0` ve `19.0` dallarında yayınlanır;
`main` dalı güncel Odoo 19 geliştirme sürümünü izler.

## DocuCraft Word PDF Report Designer

`ranvals_document_studio` sürüm `19.0.3.5.0`; belge tasarım motorunu,
Satış, Muhasebe, Satın Alma ve Odoo Studio entegrasyonlarını tek addon altında
toplar.

Öne çıkan çıktılar:

- PDF
- Gerçekten düzenlenebilir Word/DOCX
- PDF görünümünü koruyan Word/DOCX
- PNG
- Tüm biçimleri içeren ZIP
- 14 farklı kurumsal belge tasarımı
- Satış, fatura ve satın almada tek **DocuCraft Yazdır** deneyimi
- Gerçek belge verisiyle canlı tasarım ön izlemesi
- Firma dilini otomatik izleyen Türkçe, İngilizce, Almanca, Fransızca,
  İspanyolca, İtalyanca, Portekizce, Rusça ve Arapça belge/arayüz desteği
- Ayrı Önizleme/Alanlar sayfaları ve temizlenmiş otomatik alan kataloğu
- PDF, canlı ön izleme ve Word ile uyumlu 23 başlık/gövde yazı tipi
- Ekrandaki standart ve Studio alanlarını otomatik bulan yeşil/açık,
  gri/kapalı çıktı alanı seçicisi
- PDF, Word, PNG, ZIP ve arka plan işlerinde ortak alan seçimi
- Pasif şablonları bulup tek tıkla yeniden etkinleştiren Aktif/Pasif/Tümü filtreleri

Modül Odoo 19 Enterprise ve `web_studio` gerektirir. Odoo Proprietary
License v1.0 (`OPL-1`) ile lisanslanır ve Odoo Apps satış fiyatı 87 EUR'dur.
Demo kurulumu öncesinde
kök dizindeki Python bağımlılıkları yüklenmelidir:

```bash
pip install -r requirements.txt
```

Ardından Odoo uygulama listesini güncelleyip
**DocuCraft Word PDF Report Designer** uygulamasını kurun.

## Employee QR Attendance

`ranvals_hr_attendance_qr` sürüm `19.0.3.0.0`; standart Odoo Çalışanlar ve
Giriş/Çıkışlar modellerine bağlı güvenli QR mesai takibi, haftalık/aylık
puantaj, PDF/CSV raporları ve bordro hazırlık saatleri sağlar.

Modül Odoo 19 Community/Enterprise ile çalışır ve `hr_attendance`, `web` ile
Odoo 19 requirements içinde bulunan Python `qrcode` paketini kullanır. Uygulama
listesini güncelledikten sonra **Employee QR Attendance** adıyla kurulabilir.

Ayrıntılı kurulum, ağ/proxy güvenliği ve staging kabul adımları için
`ranvals_hr_attendance_qr/README_TR.md` belgesine bakın. Modül bordro girdilerini
hazırlar; maaş veya payslip oluşturmaz.
