# Toplantı notları — 9 Kasım 2026

Bu klasördeki beş görselin okuma notları ve şimdiye kadarki deneylerin
özeti. Görseller `journal/scripts/meeting_20261109_figures.py` ile
çiziliyor; her görselin altında veri kaynağı yazıyor.

## Tek cümlelik özet

Operatörü duruma göre seçmek, tur oluşturulduktan ya da bir sıçramadan
(kick) hemen sonra çok şey kazandırıyor; çözücülerin zamanının çoğunu
geçirdiği LK optimumlarında ise neredeyse hiçbir şey kazandırmıyor. Bu
tablo 100 ile 10.000 şehir arasında ve LKH-3 içinde de aynı. Ancak bütün
sayılar "seviyeye göre açgözlü" (greedy by level) politikaya karşı
ölçüldü; ayarlanmış sabit bir kaskada (2-opt → 3-opt → LK → kick) karşı
henüz ölçülmedi.

## Terimler

- **Durum (state):** Bir örnekteki bir tur. Her örnek ve tohum için 12
  tur var: rastgele, en yakın komşu, yarım kalmış 2-opt inişleri
  (d25–d90), 2-opt / Or-opt / 3-opt / LK optimumları, iki ILS turu.
- **Sınıf:** Turun yerel optimumluk seviyesi. *2-opt açık* (bir 2-opt
  hamlesi iyileştiriyor), *2-opt optimumu*, *3-opt optimumu*, *LK
  optimumu*.
- **Ölçüm A:** Her operatör, bir durumun her çapasında uygulanır;
  oran = ortalama kazanç ÷ ortalama süre. En yüksek oranlı operatör
  "lider" G(s), yani bir sonraki uygulama için açgözlü seçim.
- **E1R rollout:** Bir adayı bir dilim (ör. şehir başına 0,01 ms)
  uygula, sonra ufka kadar seviyeye göre açgözlü devam et. Saat sanal:
  her uygulama, sayılan işten kalibre edilmiş katsayılarla ücretlendirilir.
- **Oracle payı (headroom):** Açgözlü ilk seçimin ortalama boşluğu eksi
  en iyi adayınki, tur uzunluğunun %'si olarak; çapraz uydurmalı
  (tekrarların yarısında seç, öbür yarısında puanla).

## Görseller

### f1 — Lider, yerel optimumluk seviyesini izliyor (İddia 1.1)

`f1_leaders_by_class.png`, kaynak `journal/data/claim-1/e1main_classes.csv`
(E1 ana koşu, 19 Ekim, Python, 60 örnek × 12 tohum, 7.676 durum).

- **Ne gösteriyor:** Her sınıfta, hangi operatör ailesinin kaç durumda
  saniye başına en çok kazandırdığı.
- **Sayılar:** 2-opt açık (4.919 durum): %85 2-opt türevleri. 2-opt
  optimumu (796): Or-opt / 3-opt %47, LK %20, pertürbasyon %33. 3-opt
  optimumu (615): LK %34, pertürbasyon %66. LK optimumu (1.346):
  pertürbasyon %70, durumların %24'ünde hiçbir operatör iyileştirmiyor.
- **Okuma:** Önce 2-opt, sonra 3-opt veya LK, sonra pertürbasyon. Rastgele
  hamleler hiçbir durumda lider değil. Belirleyici olan optimuma uzaklık
  değil, seviye.
- **Dikkat:** Python uygulaması. Operatörler iç içe (LK 2-opt hamlelerini,
  kick'ler bir inişi içeriyor). LK optimumlarında liderlerin çoğu
  ikinciden ayrışmıyor (1.346'nın 207'si ayrışıyor).
- **Veri notu:** `e1main_classes.csv` her durumu iki kez sayıyor (toplam
  15.352 = 2 × 7.676; dört operatör sayısı günlükteki değerlerin tam iki
  katı). Paylar etkilenmiyor; etiketlerde günlükteki sayılar kullanıldı.

### f2 — Daha iyi bir ilk seçim çok değerli, ama kısa süreli (İddia 1.4)

`f2_headroom_by_class.png`, kaynak `journal/data/claim-1/e1rn_R1_summary.csv`
(E1R gecesi R1, 5 Kasım: 1.257 durum, 32 eşli tekrar, şehir başına
0,01 ms dilim, 64 dilim ufuk, durum başına ortalama 12,2 aday).

- **Sayılar (medyan oracle payı):** 2-opt açık: 1 dilimde %6,14, 2'de
  %4,22, 4'te %2,08, 8'de %0,47, 16'da %0,12. 2-opt optimumu: %1,52'den
  8 dilimde ~0'a. 3-opt optimumu: %0,12. LK optimumu: 0.
- **Açgözlü yeniliyor (Holm, 0,05):** 1 / 8 / 64 dilimde durumların
  %78 / %50 / %25'inde.
- **Okuma:** İddia 1.4 kısa ufuklarda gösterildi. Ama bu bir "önde
  başlama": açgözlü politika, en iyi ilk operatörün 1 dilimdeki
  boşluğuna medyan 2–4 dilimde, durumların %85–96'sında yetişiyor.
- **Dikkat:** R2 (0,001 ms) koşusunda pahalı inişler eksik
  ücretlendirildi (three_opt_nl 1,25×); R2 ayrı kalibre edilmeden hüküm
  için kullanılmamalı.

### f3 — Pay, optimuma uzaklıkla büyüyor

`f3_headroom_by_gap.png`, kaynak `journal/data/claim-1/aos_opportunity.csv`
(aynı R1 koşusu, 8 Kasım yeniden analizi).

- **Sayılar (1 dilim / 8 dilim medyan pay):** <%1 boşluk: 0 / 0;
  %1–2: 0 / 0; %2–5: 0,57 / 0,03; %5–10: 3,56 / 0,14; %10–20: 5,54 /
  0,56; >%20: 11,34 / 0,42.
- **Okuma:** %2'nin altında yer yok; %5'in üstünde 1 dilimde büyük pay
  var; 8 dilimde her grupta %0,6'nın altına iniyor. Uygulama için:
  operatörü tur oluşturulunca ya da bir pertürbasyondan sonra seç, geç
  aramada sabit seçime dön.
- **Dikkat:** Başlangıç boşluğu ve sınıf ilişkili (uzak turlar 2-opt
  açık); f2 ile birlikte okunmalı. Rastgele matrislerde 1 dilimde %38,8
  ama 2 dilimde %0,97: çok kısa ömürlü.

### f4 — Ölçekleme: 1k ve 10k

`f4_scaling_1k_10k.png`, kaynak `runs/aos-large/e1r-final/states.csv`
(8 Kasım, düzgün dağılımlı portgen, boyut başına bir örnek, bir durum
tohumu, 8 tekrar, 6 aday, 8 dilim ufuk).

- **Sayılar (2-opt açık, medyan pay):** 1k: %4,31 → %3,32 → %1,62 →
  %1,19 (1, 2, 4, 8 dilim). 10k: %1,75 → %1,84 → %1,00 → %0,82.
- **Okuma:** Küçük örneklerdeki eğrinin aynısı: önce birkaç yüzde, sonra
  hızla düşüş. Boyut büyüdükçe daha fazla yer açılmıyor.
- **Dikkat:**
  - Her boyutta sadece **2 durum** var; bu bir ilk bakış, sonuç değil.
  - **10k'nın saati de sapmalı:** tek doğrulama tekrarı gerçek / sanal
    süre oranında 1,35 verdi (sanal saat ~%35 eksik ücretlendiriyor).
    1k temiz (1,04–1,08). 10k'yı da çıkarmak istersen tek satır.
  - Referans tur kanıtlanmış optimum değil, en iyi kurulan ILS turu;
    pay iki boşluğun farkı olduğu için referans büyük ölçüde sadeleşiyor.

### f5 — LKH-3'te koşu ortasında ayar değiştirmenin yeri yok (İddia 4)

`f5_lkh_headroom.png`, kaynak `journal/data/claim-4/lkh_headroom_instances.csv`
(5 Kasım; tasarım ve önceden yazılmış karar kuralı `ideas/lkh-headroom.md`).

- **Soru:** LKH-3'ün MOVE_TYPE (5 veya 3) ya da KICK_TYPE (0 veya 4)
  ayarını T/2'de bir kez değiştirmek, eşit CPU süresinde en iyi sabit
  ayarı geçebilir mi? Sonuçları gören bir oracle seçiyor; o bile
  kazanamıyorsa hiçbir uyarlamalı seçici kazanamaz.
- **Tasarım:** 35 örnek, 9 aile, 1k–20k şehir; 1.000 şehir başına 2 CPU
  saniyesi; 4 tohum; 16 çizelge. Pay, V birimiyle: varsayılan ayarın
  süreyi T/2'den T'ye ikiye katlayınca kazandığı.
- **Sonuç (n ≥ 5.000, doymamış 20 örnek):** lgcp-5000 (1,11 V) ve
  lgcp-20000 (0,91 V) "yer var"; tspgen-10000 ve xmc10150 "belirsiz";
  geri kalan 16'sı "yok". Önceden yazılmış kurala göre: GO.
- **Neden özünde KILL:** Kuralda anlamlılık şartı yoktu. "Yer var" diyen
  iki örnek tek başına anlamlı değil (1,3 ve 1,8 standart hata). Mutlak
  kazanç tur uzunluğunun %0,04'ünden az. Tekrarlayan en iyi çizelge
  (önce move type 3, sonra varsayılan), 20 örneğin 14'ünde yeniden
  başlatılmış varsayılandan kötü.
- **Gerçek etki statik:** DIMACS kümeli örneklerde sabit move type 3,
  20k'da varsayılanı 1,75 puan geçiyor. Bu uyarlamalılık değil, algoritma
  seçimi (Helsgaun'un "yem kenarı" açıklamasıyla uyumlu).

## Görsellere girmeyen bulgular

### Uygulama duyarlılığı (ilk Rust gecesi, 2–3 Kasım)

Tek bir uygulama ayrıntısı değişince liderin değiştiği durumların payı:

| Değişiklik | Durum | Lider değişti |
|---|---|---|
| Mesafeler saklanmıyor, hesaplanıyor | 523 | %27,7 |
| Dizi yerine iki seviyeli liste | 205 | %21,5 |
| Aday önbelleği kapalı | 643 | %4,8 |
| Aynı durumlar yeniden zamanlandı (gürültü tabanı) | 129 | %6,2 |
| Rust ile Python E1 | 1.280 | %42,3 |

Sınıf hiç değişmedi; değişen, sınıf **içindeki** lider. Yani İddia 1.1
sağlam; seviye içindeki belirli operatöre dair iddialar uygulamaya
özgü, öyle raporlanmalı.

### Saat kapısı (E0-rs, 2 Kasım)

Medyan değişim katsayısı %0,68, ama 95. yüzdelik %3,66; 672 hücrenin 1'i
%5 sınırını aştı, 111'i belirsiz. Kapı geçilmedi. `lock-clock.sh` artık
SMT'yi ve fabric saatini de kilitliyor; E0 bununla yeniden koşulmadı.

### E1R gecesinin diğer üç cevabı (5 Kasım)

- **En iyi ilk dilim genelde bir kick:** R1'de bağsız durumların %41'inde
  lk_kick, %40'ında kick. Anlık lider, durumların yalnızca %14'ünde en iyi.
- **Dilim oranına göre seçici, açgözlüyü geçiyor:** İlk dilimdeki toplam
  kazanca göre sıralayan seçici (çapraz uydurmalı), 1 dilimde durumların
  %69'unda daha iyi, %11'inde daha kötü.
- **Dilim uzunluğu önemli:** 3-opt optimumlarında açgözlü 1 dilimde
  0,001 ms'de %10, 0,01 ms'de %45, 0,05 ms'de %71 oranında yeniliyor.

## 50k sonuçları neden çıkarıldı

8–9 Kasım gecesi 50k'da (düzgün, LGCP ve DIMACS kümeli) koşular yapıldı.
İki nedenle görsellerde yoklar:

1. **Sanal saat yanlış kalibre edilmiş.** Aynı operatör, aynı boyutta iki
   örnekte çok farklı katsayılar almış: two_opt_descent LGCP'de taşınan
   eleman başına 149 ns, kümeli örnekte 2,0 ns (75 kat fark; 149 ns
   fiziksel olarak makul değil). lin_kernighan kümeli örnekte iş terimi
   olmadan sabit 45 µs. 50k'da two_opt_descent'in tek doğrulama tekrarı,
   ücretlendirilen sürenin 0,099'unda koştu (10 kat fazla ücret).
   Düzgün 50k'da oranlar 1,05–1,72. Medyan oran (0,989) bunu
   gizliyordu: sadece 8 tekrar, çoğu 1k kick.
2. **Kümeli 50k'daki "kalıcı pay" bir takılma.** Gece raporu "kümeli
   50k'da pay 8 dilim boyunca %4,2'de kalıyor" diyordu. Gerçekte açgözlü
   politika 8 dilimin hepsinde two_opt_targeted uyguluyor (dilim başına
   ~1,3 milyon uygulama) ve boşluk sadece %9,85 → %9,66 iniyor. Takılma
   kuralı (`skip`) operatörü yalnızca kazancı **tam sıfır** olan bir
   dilimden sonra değiştiriyor; minik ama sıfır olmayan kazançlar
   bulan bir operatör hiç değiştirilmiyor. LGCP'de politika kick'e
   geçiyor ve normal iniyor.

Yine de akılda tutulacak iki şey: 50k'da da en iyi ilk dilim çoğunlukla
three_opt_nl (anlık lider değil); ve takılmanın kendisi, kazanç oranı
düşünce operatör değiştiren bir mekanizmanın (İddia 2) neden
gerekebileceğini gösteriyor.

## Proje için boşluk nerede

1. **Verim düşüşünü fark etmek (en umut verici).** Bir operatör minik
   kazançlar bulmaya devam ederken başka bir operatör hâlâ büyük kazanç
   sunuyor. Oranın düştüğünü fark edip geçmek tam olarak İddia 2'nin
   mekanizması (ör. Page–Hinkley testli DMAB). İddia 1.6 ve 5 ile de
   bağlantılı: kümeli örneklerde komşu listeleri kümeler arası kenarları
   kaçırıyor.
2. **Erken geçiş dönemi.** Tur oluşturulduktan ya da kick'ten sonra
   birkaç dilim için %3–6. Gerçek ve tekrarlanabilir, ama son boşluğu
   değil, hedefe varış süresini ve primal integrali etkiliyor. Tek başına
   mütevazı bir makale.
3. **Geç arama ve LKH.** LK optimumlarında ~0; LKH öldürme testi de aynı
   şeyi söylüyor. TSP için LKH içinde faz bazlı ayar değiştirme kapalı
   görünüyor. Denenmemiş olanlar: aday kümesi (POPMUSIC, kadran) ve
   kısıtlı varyantlar (CVRP, CVRPTW).

Olası makale çerçevesi: "Duvar saati ile ölçüldüğünde operatör seçimi,
kısa bir geçiş döneminde ve kümeli yapıda önemli; çözücülerin zamanını
geçirdiği LK optimumlarında kayboluyor", artı N1 (zaman ile değerlendirme
bütçesi sıralamayı değiştiriyor). İnandırıcı olması için sabit kaskad
karşılaştırması şart.

## Yeniden çalıştırma süreleri

Mevcut koşuların kayıtlarından tahmin. Zamanlı aşamalar kilitli saat ve
boş makine istiyor.

| Adım | Kod | Makine süresi |
|---|---|---|
| Oran tabanlı takılma kuralı; kümeli 50k d25–d90 yeniden | Değişiklik gerekli | ~40 dk |
| 50k yeniden kalibrasyon, operatör başına ≥3 doğrulama | Yok | ~30–45 dk |
| LKH doğrulama koşusu (LGCP + kümeli, 8 yeni tohum) | Yok | ~15–20 dk |
| Sabit kaskad kolu, açgözlüye karşı | Yeni kol | ~2 sa |
| `SIZES = [100, 200, 500]` düzeltmesi; 50k'da daha çok tohum | Küçük değişiklik | grafik ve tohum başına ~2–2,5 sa |
| Tam kilitle E0 saat kapısı | Yok | Tahmin edilecek |

Tam tekrarlar için: E1 ana ~5 sa, E1R gecesi 6,6 sa, düzgün 1k–50k
~9 sa, kümeli + LGCP 50k ~3,5 sa.

## Danışmana sorulacak kararlar

1. Her pay iddiasının geçmesi gereken karşılaştırma, ayarlanmış sabit
   kaskad mı olmalı? (Önerim: evet.)
2. Hangi makale: erken geçiş dönemi + zaman/değerlendirme bütçesi (daha
   az risk) mi, yoksa İddia 2 olarak verim düşüşü tespiti (daha çok iş,
   son tarihe ~11 hafta) mi?
3. İddia 4 TSP için kapatılsın mı, yoksa aday kümelerine ve CVRP'ye mi
   taşınsın?
4. Bir sonraki zamanlı koşudan önce commit edilmemiş iş commit edilsin
   mi (yol haritası kararı D3)?

GECCO 2027 son tarihi tahminen 26 Ocak 2027 civarı; resmi çağrıdan
teyit edilmeli.
