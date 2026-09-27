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

- Tek **DocuCraft Yazdır** düğmesi tasarım, belge dili ve çıktı biçimi
  seçeneklerini açar.
- Aynı pencereden PDF, düzenlenebilir native Word, PDF görünümünü birebir
  koruyan Word, PNG veya tümünü içeren ZIP oluşturulur.
- **Önizleme** ve **Alanlar** sayfaları, gerçek belge görünümü ile alan
  yönetimini birbirinden ayırır. Alanlar sayfası açık satış, fatura veya satın
  alma ekranındaki yazdırılabilir standart ve Studio alanlarını otomatik
  getirir. Yeşil alanlar çıktıya girer, gri alanlar dışarıda kalır; başlıklar
  düzenlenebilir ve alanlar sürüklenerek sıralanabilir.
- Canlı ön izleme örnek kutular yerine seçili gerçek belgenin değerlerini ve
  seçilen kurumsal tasarımı gösterir. Aynı alan seçimi PDF, iki Word türü,
  PNG, ZIP ve arka plan işlerinde korunur.
- Belge dili varsayılan olarak firma dilini izler; istenirse müşteri/tedarikçi
  dili veya elle seçilen, DocuCraft etiket sözlüğü tarafından desteklenen etkin
  bir Odoo dili kullanılabilir.
- Başlık ve gövde için 23 yazı tipi bulunur. Odoo ile paketlenen fontların ve
  güvenli sistem fontlarının karşılıkları PDF, canlı ön izleme ve
  düzenlenebilir Word çıktısında birlikte uygulanır.
- Altı yenilenmiş imza tasarımı ve sekiz yeni kurumsal tasarımla toplam 14
  yerleşim kullanılabilir.

Aynı tek işlem liste görünümünde ve Yazdır menüsünde seçili belgeler için de
kullanılabilir. Üçten fazla kayıt veya ZIP otomatik olarak **Arka Plan İşlerim** kuyruğuna alınır;
kullanıcı hazır olduğunda bildirim alır ve dosyayı yalnız kendi yetkileriyle
indirebilir.

DocuCraft yöneticileri koşullu şablon kuralları, sürüm geçmişi, güvenli JSON
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

19.0.3.0.0 sürümünden 19.0.3.1.0'a yükseltme; Odoo 19 kullanıcı yetkileri
ekranını bozan eski kategori çevirilerini güvenli adlarla değiştirir, eski
sektörel tasarım adlarını yeniler ve fazla PDF/Word menü bağlarını kaldırır.
Değişikliğin veritabanına uygulanması için yalnız kodu çekmek yerine modülü
mutlaka yükseltin ve Odoo çalışanlarını yeniden başlatın.

19.0.3.1.0 sürümünden 19.0.3.2.0'a yükseltirken de aynı şekilde veritabanı ve
filestore yedeği alın, kodu değiştirin ve `ranvals_document_studio` modülünü
yükseltin. Yükseltme; geçici alan seçici modelini ve arka plan işleri için
değiştirilemez alan seçimi anlık görüntüsünü otomatik oluşturur. Ardından Odoo
çalışanlarını yeniden başlatıp tarayıcıda sert yenileme yapın.

19.0.3.2.0 sürümünden 19.0.3.3.0'a yükseltirken modülü mutlaka yükseltin;
böylece yeni dil kaynağı, iki sayfalı yazdırma penceresi, font seçenekleri ve
güncel çeviriler yüklenir. Odoo çalışanlarını yeniden başlatın ve yeni web
varlıklarının gelmesi için tarayıcıda sert yenileme yapın.

19.0.3.3.0 sürümünden 19.0.3.3.1'e yükseltirken modülü yükseltin; böylece
pasif şablonları bulmayı sağlayan **Pasif**/**Tümü** filtreleri ve tek tıkla
yeniden etkinleştirme görünümü yüklenir. Odoo çalışanlarını yeniden başlatıp
tarayıcıda sert yenileme yapın.
