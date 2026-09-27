# Employee QR Attendance

**Teknik modül:** `ranvals_hr_attendance_qr`  
**Sürüm:** `19.0.3.0.0`  
**Hedef:** Odoo Community/Enterprise 19.0  
**Bağımlılıklar:** yalnız `hr_attendance`, `web` ve Python `qrcode`

Bu paket, eski klinik paketinden bağımsız olarak yeniden tasarlanmış QR personel
giriş–çıkış modülüdür. `shape_store_clinic` veya başka bir klinik modeline,
menüsüne, JavaScript bileşenine ya da markasına bağlı değildir. Kayıtlar Odoo'nun
standart `hr.attendance` modelinde tutulur; modül yeni Odoo/portal kullanıcı hesabı
oluşturmaz.

## Neler değişti?

- Teknik ad, modeller, tablolar, alanlar, URL'ler, çerezler ve arayüz tamamen
  `Employee QR Attendance` ürünü altında ayrıldı. Teknik modül adı yükseltme
  uyumluluğu için `ranvals_hr_attendance_qr` olarak korunur.
- Menüler standart **Giriş/Çıkışlar → QR Mesai Takibi** altında yer alır.
- QR kayıtları, bütün mesai kayıtlarını gösteren eski action yerine yalnız QR
  kaynaklı kayıtları gösterir.
- Aynı anda iki telefondan basıldığında kör “toggle” yapmak yerine imzalı ve açık
  `giriş`/`çıkış` niyeti kullanılır. PostgreSQL satır serileştirmesi, durum
  snapshot'ı, tekil nonce ve tekrar oynatma kaydı birlikte çalışır.
- İstasyon kapatma, ağ değiştirme, PIN yenileme, personel arşivleme ve telefon
  iptali oturumları kalıcı biçimde geçersiz kılar.
- Şirket ayrımı, gizli alanlar, provenance alanları, hız limitleri, gelecekteki/
  uzun süredir açık kayıtlar ve hatalı kayıt sırası savunmacı olarak doğrulanır.
- Ham IP saklama varsayılan olarak kapalıdır; User-Agent ve GPS saklanmaz.
- Mobil sayfa erişilebilirlik, kontrast, form doğrulama, yazdırılabilir QR ve
  okunamayan QR için yedek bağlantı açısından yenilendi.

## İş akışı

1. Kapıya asılan sabit QR, ilgili giriş noktasının HTTPS sayfasını açar.
2. Sunucu, isteğin izin verilen genel internet çıkış IP'sinden geldiğini denetler.
3. Personel kendisine verilen 4–20 karakterlik kod ve 6–12 rakamlık PIN ile
   kimliğini doğrular. Açık bir personel listesi veya isim araması yoktur.
4. Doğrulama tek başına mesai oluşturmaz. Personel ikinci ekranda **İşe Giriş Yap**
   veya **İşten Çıkış Yap** düğmesine basar.
5. Saat telefon saatinden değil Odoo sunucusundan alınır. Başarılı işlem standart
   `hr.attendance` kaydına ve 90 günlük teknik işlem makbuzuna yazılır.
6. Aynı form tekrar gönderilirse idempotent sonuç döner; başka eski bir form aynı
   durumu değiştirmeye çalışırsa ikinci işlem oluşturulmadan reddedilir.

## Kurulum

1. ZIP içindeki `ranvals_hr_attendance_qr` klasörünü özel eklenti dizininize veya
   Odoo.sh reponuza, klasör adı değişmeden ekleyin.
2. Önce yedek alın ve işlemi staging/development veritabanında yapın.
3. Odoo'yu yeniden başlatın, geliştirici modunda **Uygulama Listesini Güncelleyin**.
4. Uygulamalarda **Employee QR Attendance** arayın ve kurun.
5. Ayar yapacak kullanıcıya **Giriş/Çıkışlar: Yönetici** yetkisi verin.

Bu sürüm yeni teknik modüldür. Eski `shape_store_clinic_attendance` kurulu bir
veritabanında iki modülü doğrudan birlikte canlıya almayın; önce
`MIGRATION_TR.md` belgesini okuyun.

## İlk yapılandırma

### 1. Giriş noktası

**Giriş/Çıkışlar → QR Mesai Takibi → QR Giriş Noktaları → Yeni** yolunu açın.

- **Görünen Kurum Adı:** Mobil ekran ve baskıda görünecek marka/şirket adı.
- **Odoo HTTPS Adresi:** Yalnız origin yazın; örneğin `https://mesai.ornek.com`.
  Yol, kullanıcı bilgisi, query veya fragment kabul edilmez.
- **Saat Dilimi:** İşlem ekranındaki gösterim için kullanılır; kayıt UTC tutulur.
- **İzin Verilen Ağlar:** Her satıra genel IP/CIDR. Tek IPv4 için `/32`, tek IPv6
  için `/128`; IPv4'te `/24`ten, IPv6'da `/64`ten geniş ağ kabul edilmez.
- **QR Giriş–Çıkış Açık:** Ağ listesi girilip test edilene kadar kapalı tutun.

İzin vermek istediğiniz işletme ağındayken **Bulunduğum Ağı Ekle** düğmesi,
Odoo'nun gördüğü genel IP'yi ekler. `192.168.x.x`, proxy'nin iç adresi veya Wi-Fi
adı kullanılmaz.

Önemli varsayılanlar:

- hatırlanan telefon azami 30 gün;
- hatırlanan oturum boşta kalma sınırı 168 saat;
- “beni hatırla” seçilmezse geçici oturum 30 dakika;
- personel ve giriş noktası başına en fazla 5 telefon;
- iki işlem arasında en az 60 saniye;
- 16 saati aşan açık girişte otomatik çıkış yerine yönetici incelemesi.

Kaydedip etkinleştirdikten sonra **QR Baskı Sayfası** ile çıktıyı alın. Kurumun
HTTPS alan adı veya ağ politikası değiştiğinde yeni QR basın. **QR Bağlantısını
Yenile** eski baskıyı ve bütün telefon oturumlarını geçersiz kılar.

### 2. Personel kimliği

**QR Personel Kimlikleri → Yeni** yolunda şirket ve mevcut çalışanı seçin.

- Kod otomatik üretilebilir veya 4–20 ASCII harf/rakam olarak belirlenebilir.
- **Yalnız Güvenli QR Kanalı** varsayılan olarak açıktır. Açıkken bu personele ait
  standart Odoo kiosk/üst çubuk geçişi engellenir; yönetici elle düzeltme yapabilir.
- Kaydedip **PIN Oluştur / Yenile** düğmesine basın. On haneli PIN yalnız bir kez
  bildirimde gösterilir; yalnız ilgili personele güvenli kanaldan iletin.
- Personel işten ayrılırsa çalışanı arşivlemek bütün eski oturumları iptal eder.
  Yeniden etkinleştirmek eski oturumu geri getirmez.

## Zorunlu canlı öncesi ağ/proxy testi

Bu modül Wi-Fi adını değil, Odoo'nun güvenilir proxy zincirinden sonra gördüğü
`remote_addr` değerini denetler. Odoo `proxy_mode` ve Nginx/Odoo.sh güven sınırı
yanlışsa IP politikası güvenilir değildir.

- Odoo'ya doğrudan 8069 erişimini internete açmayın; yalnız güvenilir reverse
  proxy erişsin.
- Proxy, istemciden gelen sahte `X-Forwarded-*` başlıklarını temizlemeli ve kendi
  doğruladığı değeri eklemeli.
- Telefonla işletme Wi-Fi'sinde sayfayı açıp bir test çalışanıyla giriş/çıkış yapın.
- Wi-Fi'yi kapatıp mobil veride aynı QR'ı açın: 403/ağ reddi görülmeli.
- Formu Wi-Fi'de açıp, onaydan önce mobil veriye geçin: POST anında tekrar
  denetlendiği için işlem reddedilmeli.
- Sunucu iki farklı bağlantıda aynı IP'yi görüyorsa VPN/proxy topolojisini çözmeden
  o IP'yi izin listesine eklemeyin.
- HTTPS dışındaki kimlik doğrulama, işlem ve oturum yönetimi kapalıdır.

IP izin listesi tek başına fiziksel mevcudiyet kanıtı değildir. Kurum VPN'i,
paylaşılan PIN veya aynı NAT çıkışındaki uzak kullanıcı gibi riskler ayrıca
değerlendirilmelidir. Daha güçlü fiziksel doğrulama gerekiyorsa süreli/dinamik QR
ve yönetilen cihaz politikası ayrı faz olarak ele alınmalıdır.

## Güvenlik özeti

- PIN'ler açık tutulmaz: rastgele 128 bit salt ve PBKDF2-HMAC-SHA256, 600.000 tur.
  Eski 300.000+ turluk uyumlu özetler ilk başarılı girişte yükseltilir.
- Bilinmeyen kod da güncel maliyetli sahte KDF çalıştırır; hata mesajı geneldir.
- Kişi başına 15 dakikada 5 hatalı PIN sınırı bütün istasyonlarda ortaktır.
  Bilinmeyen kodlar istasyon başına ortak 30, kaynak IP istasyon başına 1.200
  istekle sınırlandırılır; sayaç tablosu rastgele kodlarla sınırsız büyümez.
- Telefon token'ı yüksek entropilidir; veritabanında yalnız SHA-256 özeti bulunur.
  Çerez `Secure`, `HttpOnly`, `SameSite=Lax` ve yalnız istasyon URL yolu kapsamındadır.
- Her state değişikliği `POST` ve Odoo CSRF doğrulaması gerektirir. Form gövdeleri
  16 KiB ile sınırlıdır ve yalnız URL-encoded form kabul edilir.
- İşlem niyeti HMAC-SHA256 imzalıdır, en fazla 5 dakika geçerlidir ve istasyon,
  kimlik, kimlik sürümü, açık giriş snapshot'ı, açık eylem ve tekil nonce içerir.
- İstasyon → kimlik → cihaz → çalışan şeklinde sabit kilit sırası ve Odoo 19'un
  transaction retry mekanizması eşzamanlı çift kaydı önler.
- Token, PIN özeti, imza anahtarı, hız limiti anahtarları ve nonce normal yönetici
  RPC/arayüzünden okunamaz. QR kaynak alanları RPC/import ile taklit edilemez.
- Şirket kayıt kuralları bütün teknik modellerde uygulanır.
- Güvenlik başlıkları: no-store, CSP, frame deny, MIME sniffing deny, no-referrer,
  noindex ve kamera/mikrofon/konum izinlerinin kapatılması.
- Ham IP yalnız yönetici özellikle açarsa başarılı mesai kaydında saklanır. User-Agent
  ve GPS saklanmaz.
- Saatlik cron süresi dolan cihazları, bir günden eski hız sayaçlarını ve 90 günden
  eski teknik makbuzları temizler. `hr.attendance` kayıtlarını silmez.

## Yönetici müdahalesi

- Uzun açık, gelecekte tarihli veya sıra tutarsız kayıtta modül tahmin yürütmez;
  işlem durur ve yönetici incelemesi ister.
- Hatalı mesai standart Odoo mesai formundan yetkili yönetici tarafından düzeltilir.
- **Telefon Oturumlarını Kapat** seçili personelin bütün oturumlarını iptal eder.
- İstasyonu devre dışı bırakmak yeni sayfa/kimlik/işlem kullanımını durdurur ve
  oturumları siler; mevcut mesai kayıtlarını değiştirmez.
- Otomatik çıkış ve diğer standart Odoo Giriş/Çıkış ayarları bu modülden bağımsızdır.

## Haftalık/aylık puantaj ve bordro hazırlığı

**Giriş/Çıkışlar → QR Mesai Takibi → Puantaj Raporu Oluştur** yolundan haftalık,
aylık veya özel tarih aralığı seçilebilir. Bordro hazırlığında **Tüm Mesai
Kanalları** kullanılmalıdır; **Yalnız QR** kapsamı sadece QR kullanım denetimidir
ve kilitlenemez.

Rapor standart `hr.attendance`, tarihsel çalışan sürümü/sözleşmesi, çalışma
takvimi, kaynak izinleri/resmî tatiller ve Odoo'nun fazla mesai onay satırlarından
hesaplanır. Sabit, bölünmüş ve gece yarısını aşan vardiyalar desteklenir. Esnek
takvimlerde yapay günlük giriş/çıkış saatleri kullanılmaz; plan, çalışma, eksik
ve plan-dışı süre dönem toplamında uzlaştırılır.

İş akışı **Taslak → İncelendi → Kilitli** şeklindedir. Açık çıkış, gelecekte/24
saatten uzun anormal kayıt ve bekleyen fazla mesai onayı kilidi engeller. Kilit
öncesinde mesai, onay, takvim veya izin kaynağının değişip değişmediği yeniden
doğrulanır. Değişiklik varsa taslağa dönüp yeniden hesaplamak gerekir; kilitli
snapshot değiştirilmez, yeni revizyon oluşturulur.

PDF özet, günlük UTF-8 CSV ve yalnız temiz/kilitli tüm-kanal dönemi için bordro
hazırlık CSV'si alınabilir. CSV formül enjeksiyonuna karşı korunur. `REGULAR`
(plan içinde çalışma), `UNDERTIME` (eksik süre) ve `OVERTIME` (Odoo'da onaylanmış
fazla mesai) birbirinden bağımsız puantaj girdileridir; doğrudan ücret formülü
değildir. Oran `0`, ücretsiz fazla mesaiyi bilinçli olarak korur.

Bu çekirdek modül maaş, vergi, izin/work-entry kodu veya bordro fişi üretmez.
Odoo Enterprise Payroll ya da ülkeye özel bordro ürünü için ayrı bir köprü
modülü, müşteri ücret politikasıyla eşleme yapmalıdır.

## Testler

Paketle birlikte bağımsız yardımcı testleri, Odoo transaction testleri, gerçek
iki-cursor PostgreSQL yarış testi ve public QWeb/asset HTTP testi gelir.

Bağımsız test:

```bash
python3 ranvals_hr_attendance_qr/tests/test_security_helpers.py
```

Odoo test veritabanında:

```bash
./odoo-bin -d disposable_qr_test \
  --addons-path=/odoo/addons,/custom/addons \
  -i ranvals_hr_attendance_qr \
  --test-enable --test-tags=ranvals_qr \
  --stop-after-init --without-demo=True
```

Testleri yalnız atılabilir veritabanında çalıştırın. Bu teslimde yapılan gerçek
doğrulamanın özeti `VALIDATION.txt` içindedir.

## Geri alma ve sınırlar

Sorunda ilk geri alma yöntemi istasyondaki **QR Giriş–Çıkış Açık** seçeneğini
kapatmaktır. Modülü veya mesai kayıtlarını canlı veritabanından doğrudan silmeyin;
önce yedek ve geri dönüş planı oluşturun.

Hiçbir yazılım için mutlak “kusursuzluk” garantisi verilemez. Bu paket; bilinen
mantık/güvenlik sorunlarına karşı yeniden tasarlanmış, Odoo 19 üzerinde kurulmuş
ve otomatik testlerden geçirilmiştir. Canlı alan adı, proxy, gerçek telefon,
kurum ağı, yerel mevzuat, KVKK bilgilendirmesi ve iş kuralları kurumun staging
kabul testiyle ayrıca doğrulanmalıdır.
