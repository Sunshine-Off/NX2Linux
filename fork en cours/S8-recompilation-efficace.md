# S8 — Recompilation complète et efficace : ce que les mesures disent (2026-11-02)

Mesures faites sur Xenoblade Chronicles 2 (0100E95004038000), du démarrage à la cinématique d'ouverture, avec `NX2LINUX_GPU=1`. Ce qui est **mesuré** est séparé de ce qui est **déduit**.

## 1. « Complète » : 27 fonctions, pas 664

`horizon_run` annonce « 664 not translated ». Le détail (`aot.unlifted`) :

| Nombre | Raison |
|---|---|
| 637 | remplacées volontairement par une fonction hôte (le SDK, `nn::*`, NVN) : ce n'est pas un manque |
| 8 + 4 + 3 | `aese`, `aesd`, `aesimc` (AES) : Remill n'a pas de sémantique |
| 4 + 2 + 1 | `sha1su0`/`sha1h`/… (SHA) |
| 4 | `pmull` (carry-less multiply, CRC / GCM) |
| 1 | `frsqrte` (estimation de 1/√x) |

Aucune de ces 27 fonctions n'a été appelée en 900 s de jeu (« not translated functions called: 0 »). Les 27 sont du code de bibliothèque (chiffrement, hachage) ; `frsqrte` est le seul qui peut arriver en plein calcul de jeu.

Pistes, de la moins chère à la plus chère :
1. **Sémantiques Remill** pour ces 8 instructions (AES-NI, SHA-NI, PCLMUL ou C portable ; `frsqrte` demande la table d'estimation exacte d'AArch64). Il faut reconstruire Remill, puis ne recompiler que les 27 fonctions (le cache objet fait le reste). Se vérifie contre Unicorn avec `module_validate.py`.
2. **Repli JIT** (dynarmic, déjà cloné, `jit=True`) : couvre aussi tout ce que Remill ne saura jamais lire, mais il faut le construire (bloqué par une permission à la session précédente).
3. Rien pour l'instant : le cas n'est pas atteint.

## 2. « Efficace » : le code recompilé n'est pas ce qui ralentit

Mesuré pendant le jeu (cinématique d'ouverture, 2,4 images par seconde) :

- **1,96 cœurs utilisés sur 16.** Sur 686 échantillons de piles (gdb, 14 prises × 49 threads) : **95 % des threads attendent** (futex).
- Parmi les threads qui tournent, **Python/ctypes domine** : sur le thread de rendu, 16 échantillons dans Python ou le pont ctypes contre 2 dans le code recompilé ; sur le thread principal, 11 contre 0.
- Charge par thread (12 s) : le thread de rendu à 73 %, le thread principal à 56 %, les autres sous 11 %. Aucun n'est saturé : ils s'attendent les uns les autres.
- Volume des appels vers Python en 900 s : plus de **10 millions d'appels NVN** (plus de 11 000 par seconde), **2,7 millions d'appels système** (3 000 par seconde), **1,07 million de dessins**.
- Le chemin des dessins (rejeu et conversion en Python) occupe **449 s sur 900** sur le thread qui soumet. Le vidage du GPU après écriture d'un tampon ne pèse que 0,5 s sur 110 s (patch 0087).
- Un profil cProfile montre environ 0,25 ms de Python par dessin dans notre code, plus de l'attente du GIL, que tous les threads invités se disputent à chaque appel hôte.

**Conclusion (déduite des mesures)** : le goulot est le **pont vers Python sous le GIL**, pas la qualité du code recompilé. Accélérer les instructions recompilées ne change presque rien tant que ce pont reste.

## 3. Plan par ordre de gain attendu

| # | Chantier | Gain attendu | Coût / risque |
|---|---|---|---|
| 1 | **Enregistrement NVN en C** : les ~11 000 appels par seconde (états, liaisons, `Draw*`) écrivent dans un tampon de commandes en C, Python ne reçoit qu'un flux binaire par soumission | retire Python de ~80 % des appels hôtes | moyen : le format des commandes existe déjà (`rec(...)` de `horizon_nvn.py`) |
| 2 | **Rejeu et conversion en C** (vers `Nx2Draw`) | supprime les 0,25 à 0,45 ms par dessin | élevé : c'est `nvn_gpu.py` (≈ 700 lignes) en C ; le Mach l'a en Objective-C (`nvn_mtl.m`) |
| 3 | **Chemins rapides des appels système en C** : `WaitProcessWideKeyAtomic`, `SignalProcessWideKey`, `ArbitrateLock/Unlock`, `WaitSynchronization`, `ResetSignal` (1,1 M + 0,9 M + 0,24 M appels) | retire ~3 000 allers-retours Python par seconde | moyen : la sémantique des mutex est celle de `horizon_hle.py`, avec le correctif du patch 0084 |
| 4 | **Première étape sans réécriture** : emballer `Nx2Draw` avec `struct.pack_into` au lieu de champs ctypes, mémoïser les conversions d'états identiques entre deux dessins | environ 2× sur la conversion | faible |
| 5 | **Recompilation complète du code** avec accès mémoire en ligne : réserver 128 Gio d'adresses virtuelles, y projeter les régions à `base + adresse`, et définir `__remill_read/write_memory_*` comme `alwaysinline` dans chaque fonction | accélère le code recompilé lui-même ; compte surtout une fois 1 à 3 faits | élevé : tout le cache objet (11 Go) est recalculé (≈ 25 min), un défaut d'accès devient un SIGSEGV à intercepter ; les adresses vues restent sous 2^37 (`0xA_xxxx_xxxx` au plus) |
| 6 | Table de hachage au lieu de la recherche dichotomique des appels indirects (`aot_find`, 17 pas sur 100 000 entrées) | faible | faible |

À ne pas faire avant les mesures : le point 5. Le code recompilé représente 2 échantillons sur 75 dans les threads qui tournent.

## 4. Défauts de rendu trouvés au passage (corrigés dans le même lot)

- **Entités et décor en silhouettes noires** : 39 % des shaders (1 502 sur 3 867) lisent le **tampon constant 1**, que le pilote remplit avec le segment de constantes du shader (octets entre `+0x700` et `+0x704` de la section de contrôle). Il était rempli de zéros. Corrigé : `nvn_spirv.shader_c1`, `GpuBackend.constant_segment`.
- **Textures chargées après leur premier usage** : une texture n'était lue qu'une fois. Une empreinte de sa mémoire (`aot_hash64`) est comparée une fois par image, et la texture est relue si elle a changé (`reuploads` dans le journal).

## 5. Étape 1 faite : l'enregistrement NVN en C (patch 0089)

`linux/host/nvnrec.c` (dans `libnx2linux_gpu.so`) fournit en C les appels chauds : liaisons (uniform, storage, vertex, texture), viewport, scissor, programme, états liés, tableaux de sommets, dessins, cibles de rendu, pochoir, `GetTextureHandle`, `Barrier`, et les setters des objets d'état (modifiés en place dans la mémoire invitée). Le jeu les appelle sans passer par Python ; `nvn_rec.py` reprend le flux binaire de chaque tampon de commandes et le décode en tuples identiques à ceux de `rec`. Désactivable : `NX2LINUX_NVN_NATIVE=0`.

Mesure (même scénario, 150 s) : **637 images contre 517** (+23 %). Le gain est modeste parce que le rejeu des dessins (étape 2) reste en Python. Les tests comparent C et Python sur des arguments aléatoires (`tests/test_nvn_rec.py`).

## 6. Étape 2 : ce que la mesure a changé (patch 0090)

Le chemin des dessins (rejeu, conversion, bibliothèque GPU) occupe ~52 % du temps du thread qui soumet ; sur ce chemin, **68 % est la conversion en Python (~0,26 ms par dessin) et 32 % la bibliothèque (~0,125 ms)**. Porter la conversion en C ne ferait gagner qu'environ ×1,5 de plus, parce que la bibliothèque et le reste du chemin restent. Le rejeu se faisait en plus **dans le thread du jeu qui soumet**, en série avec le travail du jeu.

Fait : la file NVN tourne sur son propre thread (`nvn-queue`). Un `Submit`, un `FenceSync` et un `Present` y sont rejoués dans l'ordre ; les syncs sont signalés quand la file atteint la barrière ; une texture de fenêtre n'est redonnée qu'après la présentation de sa dernière image ; `QueueFinish` attend la file vide. `NX2LINUX_NVN_ASYNC=0` rétablit le rejeu synchrone.

Mesure (150 s, même scénario) : **521 images contre 456** (+14 %), avec du bruit : deux anciens processus `horizon_run` d'une autre session tournaient encore sur la machine pendant les essais. Restent, par ordre de gain : copies évitables (les tampons du jeu sont lus par Python puis recopiés deux fois), conversion en C, coût de la bibliothèque par dessin (mutex global, tampons uniformes de 64 Ko recopiés à chaque dessin).

## 7. Où le temps passe après la file asynchrone (patchs 0091, 0092)

Mesure sur 75 s de jeu (cinématique d'ouverture, ≈ 4,4 images par seconde, 1,7 cœur utilisé sur 16, aucun thread saturé) :

- le thread de la file est **occupé 55 % du temps et inactif 33 %** : le jeu ne le nourrit pas assez vite ; sur ses 41 s occupées il ne passe que 21 s sur le CPU (le reste : attente du GIL ou du GPU) ;
- les threads du jeu **attendent la présentation de l'image précédente 20 % du temps** (acquisition d'une texture de fenêtre) ;
- donc le pipeline est limité par la **latence entre les deux côtés** (une image en vol), pas par un thread saturé : ≈ 0,14 s de rejeu par image + ≈ 0,1 s de travail du jeu non recouvert.

Pour atteindre les 30 images par seconde du jeu, il faut ≈ 33 ms par image, soit pour ≈ 470 dessins environ 60 µs par dessin côté rejeu (aujourd'hui ≈ 290 µs) **et** réduire d'autant le travail du jeu : ni l'un ni l'autre ne s'obtient par de petits réglages (les copies de tampons et l'intervalle de bascule du GIL ont été essayés sans effet). Les deux chantiers qui restent : conversion et rejeu des dessins en C (≈ ×4 côté rejeu), puis recompilation avec accès mémoire en ligne (côté jeu).

Rendu : le segment c0 est rempli comme sur l'hôte Mach (taille de point maximale, position d'échantillon, instance de base, comptes de groupes de calcul). Les images de la cinématique d'ouverture montrent des passages sombres par construction (génériques de crédits) ; je n'ai pas trouvé de preuve qu'une scène reste trop sombre une fois le tampon constant 1 corrigé.

## 8. Étape 2 faite : la conversion et le rejeu des dessins en C (patch 0093)

L'échantillonnage gdb du thread de rejeu avait montré que **44 % de son temps est de l'attente du GIL** (chaque appel ctypes rend le GIL, et le reprendre attend un thread du jeu), 18 % de l'attente du GPU et presque rien dans la bibliothèque C. La conversion en Python n'était donc pas seulement lente : elle forçait ~10 allers-retours par dessin. Le port en C les supprime :

- `linux/host/nvnreplay.inc` (inclus par `nvnrec.c`, même bibliothèque que le GPU) garde l'état qu'un dessin lit (tampons, textures, états, cibles) à partir des flux enregistrés, et appelle directement `nx2gpu_draw`. Les structures `Nx2*` sont dans `nx2gpu_types.h`.
- Ce qui demande les objets du jeu est demandé à Python **une fois** par programme, par image de rendu et par liaison de texture (celle-ci une fois par image affichée, pour relire une texture que le jeu a écrite entre-temps) ; `nvn_replay.py` répond.
- Ce que C ne fait pas (programme à images de stockage, dessin indirect, dessin de texture) revient à Python avec l'état exporté de C ; clears, copies et dispatchs aussi.
- **Vérification** : `NX2LINUX_NVN_REPLAY=check` fait passer chaque flux par les deux chemins et compare chaque dessin octet par octet (structure et mémoire pointée). Sur 35 000 dessins réels de Xenoblade : 7 écarts, tous de la même nature (une texture que le jeu venait d'enregistrer entre les deux passes), aucun écart de format, d'état ou de tampon. Ce mode a trouvé un vrai défaut (`cbuf_slot` = -1 lu comme non signé), corrigé.
- `NX2LINUX_NVN_REPLAY=py` rétablit le rejeu en Python.

Mesure (100 s, deux essais chacun) : **446 et 455 images contre 357 et 364 en Python (+25 %)**. Le thread de rejeu est maintenant inactif 49 % du temps et le jeu n'attend plus que 3 s sur 100 la présentation : **le goulot est passé du côté du jeu**.

## 9. Le goulot côté jeu : les réveils entre threads (patch 0094)

`NX2LINUX_SVC_PROFILE=1` chronomètre les appels noyau qui bloquent plus de 0,5 ms, par site d'appel. Sur la cinématique d'ouverture (110 s) : la mise à jour du jeu (`fw::Framework::update`) attend huit fois par passage la fin du pool de threads d'animation (`anm::ThreadPool::waitForCompletion`), ≈ 2 500 passages × ≈ 1,7 ms par attente, soit ≈ 35 s, alors que le travail du pool est minuscule. Cause : tous les threads attendaient sur **une seule condition** et chaque signal faisait `notify_all` ; `SignalProcessWideKey` prenait 1,85 ms par appel (les ≈ 38 threads du jeu se réveillaient, se disputaient le verrou et le GIL).

Correctif : un verrou pour l'état du noyau, mais chaque thread qui attend se parque sur **un événement à lui** (`Hle.park`) ; le déverrouillage d'un mutex, le signal d'une clé, le signal d'un événement et la fin d'un thread ne réveillent que les threads concernés. Six tests (clé A ne réveille pas B, attente de plusieurs événements, délai, événement à remise à zéro automatique, signal venu de l'hôte).

Mesure (même scénario, 110 s) : **339 images avant, 1 407 après (×4,1)**. Un run de 280 s donne 3 398 images (≈ 12 images par seconde de moyenne depuis le démarrage), sans thread arrêté ni erreur de rappel.

## 10. Accès mémoire en ligne (patch 0095, `AOT_INLINE_MEM=1`)

Échantillonnage gdb après le patch 0094 (25 instantanés, 1 250 échantillons de threads) : 97 % attendent ; sur les ≈ 39 qui tournent, les accesseurs `__remill_*_memory_*`, `__tls_get_addr` (compteurs par thread) et `feclearexcept` pèsent près de 70 %, le code recompilé lui-même presque rien.

Le patch réserve tout l'espace invité (2^37 octets, `nx2_gbase`), place chaque région à `nx2_gbase + adresse`, et définit les accesseurs `alwaysinline` dans chaque fonction (add + accès `volatile`, pas de compteur). Un accès hors région fait un SIGSEGV que le gestionnaire convertit en l'erreur habituelle (sans taille ni sens). Pas avec le JIT.

Recompilation complète : **1 430 s** (97 763 objets, cache de plus ≈ 12 Go). Mesure (110 s, trois essais alternés) : **1 710–1 734 images contre 1 587–1 600 (+8 %)**. Gain modeste, comme prévu : le jeu attend plus qu'il ne calcule. Reste opt-in.

## 11. Les « 7 mises à jour par image » : hypothèse réfutée

Le désassemblage de `ml::WorkRootState::runSingle` montre une itération de la boucle principale par image (beginFrame, un seul `updateWork`, endFrame), et `FrameworkLauncher::update` n'appelle `Framework::update` qu'une fois. Les ≈ 13 « mises à jour » par image que j'avais déduites venaient du nombre d'**appels noyau** de chaque phase du pool d'animation (un signal et une attente par thread de travail, ≈ 13 threads), pas de mises à jour du jeu. Il n'y a donc pas de rattrapage de pas de temps fixe.

Ce qui reste vrai : chaque image fait 8 phases du pool × ≈ 13 signaux et attentes, soit ≈ 200 appels noyau passant par Python, ≈ 0,3 ms l'attente en profil. Le chemin rapide en C des appels de synchronisation (étape 3 du plan) vise exactement cela.

Nouvelles variables d'environnement du profil : `NX2LINUX_SVC_PROFILE_MIN` (secondes, 0 = tous les appels), `_DEPTH`, `_WIDTH`, `_ROWS`.

## 12. Lancement avec fenêtre : ce qui s'affiche

Run de 150 s avec `NX2LINUX_DUMP_FRAME` (une image sur 30) : noir jusqu'à ≈ 21 s (chargement), écran-titre « Press any button » ensuite, puis l'ouverture (ciel, créature, génériques). Aucun blocage dur. Points à examiner : le titre s'affiche sans logo ; la créature de l'ouverture est une silhouette noire uniforme (textures de remplacement ? `dummy: view type 1 of a 3D image` 62 dessins, `dummy: unknown texture` 27, `upload: depth` 37) ; des appuis de 0,2 s sur A sont perdus à 11 images/s alors que des appuis de 1 s passent (le tampon partagé ne garde que l'état le plus récent).

## 13. Saut de la cinématique : deux défauts du recompilateur, pas « la suite du jeu » (patch 0097)

Le jeu entier est recompilé (103 435 fonctions, `main` + `sdk` + `subsdk0`) : la suite existe et tourne (premier combat, Rex contre King Crustip, HUD compris). Le plantage au saut venait de deux défauts, trouvés avec un scénario scripté (`NX2LINUX_PRESS`: A pour quitter le titre, Plus pour la pause, A pour sauter) :

1. **Pile du thread principal de 64 Kio** (`ModuleSet.STACK_SIZE`) : `fw::ColiCheck::testLineChara` garde un `TColiCheckResult<256>` sur la pile et la dépasse dès le premier terrain construit (« fault in nnMain: guest access at 0xffec90 … »). Elle fait 8 Mio.
2. **Table de sauts mal bornée** (`btl::EnemyParameter::HateProc`) : une garde `fcmp ; and ; ccmp w8, #9, #2, lt ; b.hi` prenait comme borne un `cmp w0, #0` plus ancien (2 entrées au lieu de 10), d'où « indirect jump to 0x806b8ec: not the entry of an AOT function » dans le premier combat. 172 cibles de plus dans les trois modules ; **les manifestes du cache doivent être refaits** (`elf_module.describe(data, name, elf, build_id)` pour chaque ELF, écrire le JSON, puis un lancement ne recompile que les nouvelles entrées : 172 objets).

## 14. Les silhouettes noires : des textures sur une même mémoire (patch 0098)

Méthode : rejeu Python (`NX2LINUX_NVN_REPLAY=py`) pour vider chaque cible (`NX2LINUX_GPU_DUMP`, maintenant aussi R11G11B10F, RGB10A2, RGBA16F, R32F, et les profondeurs), journal des dessins à partir d'une image (`NX2LINUX_DRAW_LOG_FROM`), désassemblage SPIR-V des shaders (`spirv-dis`). Le rendu est différé : G-buffer à 5 cibles, puis 5 dessins plein écran d'éclairage qui n'éclairent que les pixels dont la profondeur est **égale** (test EQUAL) à un identifiant de matériau (1 à 5, sur 16 bits) écrit avant par une passe qui lit le G-buffer et le **stencil** du tampon de profondeur. Trois défauts en chaîne :

- Le jeu crée **plusieurs textures NVN sur la même mémoire** (le tampon de profondeur, ses vues pour l'échantillonner, une carte d'ombre et la vue qui la lit). Chacune devenait une image GPU distincte : celles qu'on échantillonnait n'étaient jamais écrites (nulles). Elles partagent maintenant une image (même mémoire, format, taille).
- `nvnTextureBuilderSetDepthStencilMode(STENCIL)` n'était pas gardé : on échantillonnait la profondeur au lieu du stencil. Une texture en mode stencil est lue par l'aspect stencil (`Nx2Tex.aspect`, `gpu.c`).
- Résultat : la passe d'identifiants écrivait 0 partout, aucun éclairage ne s'appliquait à la géométrie (silhouettes noires), la mer de nuages de l'ouverture manquait. Le même défaut est documenté pour l'hôte Mach (`games/0100F43008C44000/README.md`, éclairage différé en bandes).

Vérifié sur le premier combat (grue, moulin, tonneau, rochers texturés et éclairés) et sur l'ouverture (mer de nuages, logos). Les textures d'un même emplacement mais de formats différents ne sont pas fusionnées (à traiter si un jeu en a besoin).
