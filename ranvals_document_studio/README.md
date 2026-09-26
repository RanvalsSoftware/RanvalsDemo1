# DocuCraft All-in-One — Odoo 19 Enterprise

Bu dağıtım gerçek anlamda **tek Odoo modülüdür**. `ranvals_document_studio`
klasörü; çekirdek motoru, Satış, Muhasebe, Satın Alma ve Studio rapor seçiciyi
birlikte içerir.

## Yeni kurulum

1. Yalnız `ranvals_document_studio` klasörünü Odoo addons dizinine kopyalayın.
2. Uygulama listesini güncelleyin.
3. **DocuCraft – All-in-One Document Designer for Odoo** uygulamasını kurun.

Odoo; `sale_management`, `account`, `purchase` ve Enterprise `web_studio`
bağımlılıklarını otomatik kurar. Bu nedenle bu paket Odoo 19 Enterprise içindir.

Python gereksinimlerinin uyumlu sürüm aralıkları paket kökündeki
`requirements.txt` içinde sabitlenmiştir: `python-docx>=1.1.2,<2`,
`pypdfium2>=4.30,<6` ve `Pillow>=9`. Odoo manifesti bu üç dağıtımın kurulu
olduğunu ayrıca denetler.

## Kullanım

Satış siparişi, fatura/iade veya satın alma siparişi formunda:

- **PDF İndir** seçilen etkin/koşullu DocuCraft tasarımını üretir.
- **Word İndir** metin ve tabloları düzenlenebilir native DOCX üretir.
- **Diğer Biçimler** penceresi PDF, düzenlenebilir Word, PDF görünümünü birebir
  koruyan Word, PNG ve tümünü içeren ZIP seçeneklerini açar.

Aynı işlemler liste görünümünde seçili belgeler için Yazdır menüsündedir. Üçten
fazla kayıt veya ZIP otomatik olarak **Arka Plan İşlerim** kuyruğuna alınır;
kullanıcı hazır olduğunda bildirim alır ve dosyayı yalnız kendi yetkileriyle
indirebilir.

Belge Studio yöneticileri koşullu şablon kuralları, sürüm geçmişi, güvenli JSON
içe/dışa aktarma ve gerçek kayıtla önizlemeyi kullanabilir. Veritabanı
yöneticileri Yönetim altında Sistem Sağlığı ve genel Saklama Politikası
ekranlarını görür.

## Eski beş-modüllü kurulumdan yükseltme

Tam veritabanı ve filestore yedeği alın. Dört eski bağlayıcı klasörünü
(`ranvals_document_studio_sale`, `ranvals_document_studio_account`,
`ranvals_document_studio_purchase`, `ranvals_document_studio_studio`) addons
yolundan kaldırın; çekirdek klasörü bu All-in-One klasörle değiştirin ve
`ranvals_document_studio` modülünü yükseltin. Birleştirme migrasyonu eski dış
kimlikleri yeni tek-modül ad alanına taşır. Yükseltme tamamlanınca Odoo'yu
yeniden başlatın.

19.0.2.0.0 sürümünden 19.0.3.0.0'a geçerken de veritabanı/filestore yedeği
alın, bu klasörü değiştirin ve yalnız `ranvals_document_studio` modülünü
yükseltin. Migrasyon mevcut şablonlar için başlangıç sürümü oluşturur; hiçbir
belge veya şablon verisini sessizce kesmez.
