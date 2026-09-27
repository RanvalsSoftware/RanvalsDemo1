# Eski Shape Store QR Modülünden Geçiş

Yeni teknik modül `ranvals_hr_attendance_qr`, eski
`shape_store_clinic_attendance` modülünün yerinde güncellemesi değildir. Kullanıcı
bağımsız bir modül istediği için bütün teknik kimlikler değiştirilmiştir:

- modeller: `clinic.attendance.*` → `ranvals.qr.*`;
- mesai kaynak alanları: `clinic_qr_*` → `ranvals_qr_*`;
- URL: `/clinic/attendance/...` → `/ranvals/qr-attendance/...`;
- çerez, XML-ID, menü ve asset adları değişmiştir;
- yeni güvenlik politikaları ve tablo alanları eklenmiştir.

## `ranvals_hr_attendance_qr` 19.0.2 sürümünden yükseltme

19.0.3 sürümünde mağaza/görünen ad **Employee QR Attendance** olmuştur; teknik
modül adı özellikle değiştirilmemiştir. Mevcut modül klasörünü aynı teknik adla
güncelleyip staging veritabanında `-u ranvals_hr_attendance_qr` çalıştırın.
Yükseltme, eski standart mesai satırlarında boş olan şirket snapshot alanını
çalışanın yükseltme anındaki şirketiyle doldurur. Daha önce şirket değiştirmiş
çalışanların eski kayıt şirketleri yalnız mevcut veriden kesin çıkarılamayacağı
için bu satırlar müşteri kabulünde ayrıca kontrol edilmelidir.

## Yeni/fresh kurulum

Eski modül hiç kurulmadıysa yalnız `ranvals_hr_attendance_qr` kurun. Klinik ana
modülüne ihtiyaç yoktur.

## Eski modül üretimde kuruluysa

Bu paket bilinçli olarak otomatik ve sessiz SQL veri taşıması yapmaz. Bilinmeyen
müşteri şeması üzerinde otomatik tablo kopyalamak mesai geçmişini bozabileceği için
geçiş staging veritabanında planlanmalıdır.

1. Tam PostgreSQL ve filestore yedeği alın; geri yüklemeyi doğrulayın.
2. Staging kopyasında eski QR giriş noktalarını kapatın.
3. Yeni modülü kurun; yeni giriş noktalarını ve ağ politikalarını elle doğrulayın.
4. Personel kimliklerini yeni modülde oluşturun ve yeni PIN verin. Eski telefon
   token'larını taşımayın.
5. Yeni QR'ları basın. Teknik URL ve çerez yolu değiştiğinden eski baskı ve
   hatırlanan oturumlar bilinçli olarak geçersizdir.
6. Standart mesai kayıtları zaten `hr.attendance` içindedir ve kaybolmaz. Ancak
   eski `clinic_qr_in_station_id` / `clinic_qr_out_station_id` kaynak bilgisi yeni
   alanlara otomatik kopyalanmaz. Bu provenance geçmişi gerekiyorsa müşteri
   veritabanı yedeği üzerinde ID eşleme scripti hazırlanıp ayrıca test edilmelidir.
7. Yeni akışın ağ dışı red, giriş, çıkış, çift tıklama, PIN iptali ve eşzamanlı
   iki telefon kabul testleri geçmeden canlıya geçmeyin.
8. İki public QR controller'ını uzun süre birlikte açık bırakmayın. Yeni akış
   onaylandıktan sonra eski istasyonları kapalı tutun; eski modülü kaldırma kararını
   yalnız yedek ve müşteri verisi incelemesinden sonra verin.

Eski modülün klasörünü yeni klasörün üzerine açmayın ve klasör adını değiştirerek
“upgrade” yapmayın. Odoo teknik modül adı, model tabloları ve XML-ID'leri buna uygun
bir migration olmadan yeniden adlandırılamaz.
