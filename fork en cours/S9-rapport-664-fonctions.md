# Les 664 fonctions « non traduites » de Xenoblade Chronicles 2 (état du 2026-11-03)

Source : le journal de construction (`built in … 664 not translated`), le fichier de métadonnées du cache (`aot-cache/lib/<clé>.json`, adresse → raison) et les tables de `horizon_overrides.py` / `horizon_trivial.py`. Les chiffres ci-dessous sont mesurés sur la construction actuelle (103 435 fonctions, modules `main`, `sdk`, `subsdk0`).

## 1. Ce que sont ces 664 fonctions

| Catégorie | Nombre | Raison |
|---|---|---|
| **Remplacées par une fonction de l'hôte** | **637** | Le code du SDK qui parle aux services système de la Switch ne peut pas tourner tel quel sous Linux : on branche à la place une fonction Python (HLE) à l'adresse de la fonction. Ce n'est pas un manque du recompilateur. |
| **Non liftables par Remill** | **27** | Instructions sans sémantique dans Remill : AES (`aese`, `aesd`, `aesimc`), SHA-1 (`sha1h`, `sha1su0`), SHA-256 (`sha256su0`), `pmull` (GCM), `frsqrte`. |

### Les 637 remplacées, par origine

| Origine | Nombre | Ce que fait la fonction hôte |
|---|---|---|
| Écrites à la main (`horizon_overrides.TABLE`) | 230 | Implémentation réelle : `nn::audio` 77, `nn::hid` 56, `nn::fs` 30, `nn::oe` 20, `nn::account` 15, `nn::vi` 10, `nn::time` 9, `nv::*` 6 (graphique), le reste 7. |
| « Triviales » (venues de l'hôte Mach) | 164 | Ne font rien ou rendent une constante : `nn::friends` 26, `nn::ldn` 21, `nn::hid` 20, `nn::account` 12, `nn::oe` 9, … |
| **« Pas portées » (`STUBBED`)** | **244** | **Répondent 0 sans rien faire.** C'est la partie qui peut manquer au jeu : `nn::nfp` (amiibo) 26, `nn::hid` 26, `nn::audio` 22, **`movie::Decoder` 20 (décodage vidéo)**, `nn::socket` 17 + `nnsocket*` 11 (réseau), `nn::swkbd` 15 (clavier à l'écran), `nn::mii` 12, `nn::account` 10, `nn::time` 9, `nn::ec` 8, `nn::friends` 7, … |

(230 + 164 + 244 = 638 pour 637 : une fonction n'est pas exportée sous le même nom dans les deux tables.)

### Les 27 non liftables

Toutes dans `sdk`, toutes de `nn::crypto` sauf une :

- AES : `AesImpl<16|24|32>` (Initialize, EncryptBlock, DecryptBlock), `CbcModeAes128Helper`, `CtrModeImpl`, `XtsModeAes128Helper` ;
- SHA-1 (`Sha1Impl` : Update, ProcessBlock, ProcessLastBlock) et SHA-256 (`Sha256Impl` : Update, ProcessBlocks, GetHash, ProcessLastBlock) ;
- GCM (`GcmModeImpl` : UpdateAad, Encrypt, Decrypt, et une fonction d'aide à `pmull` appelée par 14 autres) ;
- `nn::hid::SixAxisSensorState::GetQuaternion` (`frsqrte`, capteur de mouvement).

## 2. Comment elles interagissent avec le reste

- **Les 637 remplacées** sont appelées par le jeu comme n'importe quelle fonction ; le branchement se fait par l'adresse (table d'imports de l'exécution, `aot_register_import`), donc le code appelant est recompilé normalement et ne voit pas la différence. Une fonction « pas portée » rend 0 : selon la fonction, le jeu l'interprète comme un succès ou comme « rien ici ».
- **Les 27 non liftables** deviennent un *stub* (`aot_unlifted`) : sans JIT (notre cas), un appel arrête le thread du jeu avec l'adresse et la raison. Avec un JIT (dynarmic, déjà dans le dépôt), l'appel passe par lui.
- **Mesure** : 150 s de jeu (démarrage, titre, début de l'ouverture) : `not translated functions called: 0`. Aucune des 27 n'a été appelée. Des 244 « pas portées », seules 2 l'ont été (`nn::hid::SetNpadJoyHoldType`, `nn::hid::GetVibrationDeviceHandles`), sans conséquence visible.
- Je n'ai pas pu établir par analyse statique *qui* les appelle : les appels du jeu vers le SDK passent par des stubs d'import (`blr` via la table des symboles), pas par des `bl` directs, donc le graphe d'appels direct est vide pour toutes. Une mesure d'exécution (compteur par fonction) est la bonne méthode.

## 3. Importance, par ordre

1. **`movie::Decoder` (20 « pas portées »)** : tout ce qui est vidéo. Si le jeu lit des films préenregistrés (ouverture, certaines scènes), ces appels rendent 0 et rien ne se décode. **Non vérifié** : aucun de ces appels n'a eu lieu dans les 150 s testés.
2. **`nn::hid` (56 + 26 + 20)** : la manette. 2 appels « pas portés » ont déjà eu lieu ; vibrations, mode de tenue des Joy-Con, capteurs de mouvement.
3. **`nn::audio` (77 écrits + 22 pas portés)** : le moteur audio tourne (77 fonctions réelles) ; les 22 « pas portés » sont l'entrée audio et les notifications.
4. **`nn::crypto` (27 non liftables)** : utiles seulement si le jeu chiffre ou hache pendant l'exécution (sauvegardes, vérifications). Rien constaté jusqu'ici.
5. **`nn::swkbd`, `nn::mii`, `nn::nfp`, réseau, comptes** : fonctions annexes (clavier à l'écran, Mii, amiibo, en ligne) : à porter si le jeu y accède.

## 4. Début d'une solution

### Les 27 non liftables
- **Sémantique dans Remill** (la méthode déjà utilisée pour `ldxr`/`stxr`, voir `remill-patches/`) : ajouter AES (AESE, AESD, AESMC, AESIMC), SHA-1 (SHA1C/P/M/H/SU0/SU1), SHA-256 (SHA256H/H2/SU0/SU1), PMULL, et FRSQRTE. Chaque instruction devient un appel à une fonction du runtime (`__nx2_aese(state, key)`, …) définie dans `aot_runtime.cpp` ; l'implémentation est soit portable (tables S-box, pseudo-code ARM de `FPRSqrtEstimate`, exactement celui de l'ARM ARM pour que le résultat soit identique bit à bit), soit les instructions x86 (AES-NI, PCLMULQDQ, SHA-NI) quand le processeur les a. Gain : les 27 fonctions et toute future fonction cryptographique du même genre.
- **Plus rapide, moins général** : remplacer les fonctions `nn::crypto` (hôte) par des appels à une bibliothèque (OpenSSL). Évite Remill, mais une fonction à la fois.
- **Vérification** : comparer chaque instruction à Unicorn sur des vecteurs de test, comme `test_aot_simd.py` le fait pour NEON.

### Les 244 « pas portées »
1. **Compter les appels réellement faits** : le journal `[stub] … is not ported, answers 0` existe déjà pour le premier appel de chaque ; sur une partie de plusieurs heures, la liste des fonctions appelées se réduira à quelques dizaines.
2. **Porter par priorité** : `movie::Decoder` d'abord (décodage via ffmpeg/libav ou VAAPI, sortie vers un tampon que le jeu lit), puis les manques de `nn::hid` constatés.
3. Les autres restent « répondre 0 » tant qu'aucun appel n'est vu.

## 5. Jalon A : toolchain reproductible (2026-11-04)

- `tools/setup_toolchain.sh [PREFIX]` installe, hors du dépôt : Remill `56918a8` + les 8 patches de `remill-patches/` (`git am`), dynarmic `a466015` (sous-modules, `-fPIC`, sans `-Werror`), et vérifie LLVM 22.x. Il écrit `PREFIX/env.sh` (`REMILL_BUILD`, `DYNARMIC_DIR`, `CPLUS_INCLUDE_PATH` si Boost a été dépaqueté).
- `horizon_run.py --jit` : construit le repli dynarmic ; une des 27 fonctions non liftables s'exécute alors au lieu d'arrêter son thread. Les accès mémoire en ligne sont coupés avec le JIT.
- Preuve dans un préfixe vide (`~/.cache/nx2linux/toolchain-clean`) : installation complète sans intervention, puis `test_aot_svc test_aot_realistic test_aot_stub test_elf_module test_aot_jit test_aot_simd test_aot_memory test_jump_tables` : 91 tests, 1 échec au premier passage (`faddp` ajouté au patch 0008 mais pas à la liste figée des écarts de drapeaux ; corrigé, patch 0100), puis `test_aot_simd` OK.
- Reste : une construction complète de Xenoblade 2 avec `--jit` (clé de cache différente, tout se relifte) n'a pas été lancée ; les 27 fonctions ne sont donc pas encore prouvées sur le jeu (jalon B).

## 6. Jalon B : les 27 fonctions non liftables (2026-11-04)

Résultat : **0 des 27 fonctions de `nn::crypto` et `nn::hid` reste non traduite** (mesure : `lift27` ci-dessous, sur le module `sdk` réel de Xenoblade 2).

Ce qui a changé :
- Remill, patch `remill-patches/0009` : décodeurs et sémantiques de `AESE`, `AESD`, `AESMC`, `AESIMC`, `SHA1C/P/M/H/SU0/SU1`, `SHA256H/H2/SU0/SU1`, `PMULL`/`PMULL2` (8 bits et 64 bits), `FRSQRTE` (scalaire et vecteur, simple et double), `FRECPS` et `FRSQRTS` (marches de Newton, scalaire et vecteur). `FRSQRTS` n'était pas dans la liste de départ : une des 27 fonctions (le calcul d'un quaternion, `nn::hid`) l'utilise.
- Les pas AES appellent `nx2_aes` (patch moteur 0101, `aot_runtime.cpp`) : la S-box est calculée depuis sa définition (inverse dans GF(2^8) puis transformation affine), sans table recopiée. SHA-1, SHA-256, PMULL, FRSQRTE, FRECPS et FRSQRTS sont du code entier ou flottant écrit d'après le pseudo-code du manuel Arm.
- Aucun code ni donnée du jeu dans le dépôt.

Preuves (environnement propre du jalon A, Remill 0001–0009) :
- `test_aot_simd` : 14 tests, dont le lot crypto (20 instructions, 200 cas chacune), `frsqrte` (valeur **et** drapeaux FPSR, 5 formes, plus de 500 cas par forme, zéros, infinis, NaN, dénormalisés), `frecps`/`frsqrts` (64 cas sans NaN, 200 avec NaN) et le vecteur AES-128 de FIPS-197 annexe C.1, tous identiques à Unicorn (sauf le vecteur FIPS, qui est la réponse publiée). Les autres suites (AOT, JIT, SVC, tables de sauts, module ELF, mémoire : 92 tests) passent aussi avec le nouveau Remill et le nouveau runtime.
- Les 27 vraies fonctions, liftées puis comparées à Unicorn par le worker de `module_validate.py` (800 cas aléatoires chacune, avec leurs appelés) : **26 égales, 1 non concluante** (`AesImpl::EncryptBlock` d'AES-128, dont tous les cas aléatoires font une faute des deux côtés parce qu'elle attend un pointeur vers une structure). Cette dernière a été exécutée à part avec des clés d'aller valides : elle produit le chiffré du vecteur FIPS-197 (`69c4e0d8…c55a`).
- Ce qui n'est pas prouvé : l'exécution de ces fonctions par le jeu lui-même (aucune n'est appelée dans le parcours testé), et les drapeaux FPSR de `frecps`/`frsqrts` (les valeurs sont comparées, pas les drapeaux ; un SNaN ne lève peut-être pas IOC).

Limites connues (écrites, pas contournées) :
- Les formes demi-précision de `FRSQRTE`, `FRECPS`, `FRSQRTS` restent non décodées : `LiftError` explicite si un jeu en utilise. `FRECPE` et `FRECPX` aussi : à faire si un recensement (`module_validate.py census`) les montre.
- `FRSQRTE`/`FRSQRTS` ignorent `FPCR.FZ` (mode de vidage des dénormalisés) : le jeu ne l'active pas, `Unicorn` non plus par défaut.
- `FRSQRTS` : la moitié de `(3 - a*b)/2` est faite sur un opérande avant le produit pour ne pas déborder ; pour deux opérandes minuscules le résultat passe par le chemin fusionné classique.

## 7. Jalon C : un profil par jeu, et le rapport de couverture (2026-11-04)

Ce qui a changé (patch moteur 0102) :
- `horizon_game.py` : tout ce qui est propre à un titre vient de `dm/games/<id>/` (le dossier que l'hôte Mac et les outils de shaders lisent déjà, via `game.py` ; `DM_GAMES_DIR` ajoute des dossiers hors dépôt). Clés lues : `program_id`, `modules` (ordre de chargement, sans `rtld`), `sys_resource`. Un fichier optionnel `linux_profile.py` porte les contournements du jeu. `PROFILES` a disparu de `horizon_run.py`, l'identifiant `0x0100E95004038000` de `svc_GetInfo` aussi. `horizon_profile_xc2.py` est devenu `dm/games/0100E95004038000/linux_profile.py` (vide : aucun contournement n'est nécessaire).
- Un titre sans profil tourne avec des valeurs Horizon génériques et le dit (`[profile] no profile …`).
- `survey.py` (et `nx2linux.py survey -- …`) : pour un jeu, combien de fonctions sont liftées, remplacées (par origine et par espace de noms), repliées sur le JIT (`--jit`), non traduites avec leur raison, et combien sont sans raison ; `--census` ajoute les classes d'instructions que Remill ne lift pas.

Mesures sur Xenoblade 2 (103 665 fonctions : main 49 909, subsdk0 13 379, sdk 27 342) :
- Avec l'ancien Remill (sans 0009) : 637 remplacées (230 écrites à la main, 164 triviales, 243 « pas portées »), **27 non traduites** (instructions sans sémantique : aese 8, aesd 4, sha256su0 4, pmull 4, aesimc 3, sha1h 2, sha1su0 1, frsqrte 1), 0 sans raison. C'est le rapport S9 retrouvé par une commande.
- Avec le Remill du jalon B, construction complète à froid (1 541 s sur 16 cœurs, 0 en cache) : 103 028 liftées, 637 remplacées, **0 non traduite, 0 sans raison**.
- Le jeu lancé avec le profil : 211 trames présentées en 60 s, comme avant.

Écart trouvé en route : brancher `app_pool` / `mem_used` du profil sur `GetInfo` (types 6 et 7) fait échouer `mtl::MemManager::Region::addChild` au démarrage de Xenoblade 2. Ces deux clés décrivent la partition mémoire de l'hôte Mac ; l'hôte Linux garde ses totaux. Seuls `program_id` et `sys_resource` viennent du profil.

Le jeu lancé avec l'ordre du profil (`main subsdk0 sdk`, sans `--order`, cache complet du nouveau Remill) : 507 trames présentées en 60 s, 637 fonctions non traduites au sens « remplacées », aucun arrêt. Reste : la liste des 243 fonctions « pas portées » n'est pas encore rapprochée de ce que le jeu appelle réellement (jalons D et E).

## 8. Jalon D : le chemin graphique, ce qui est refait et ce qui est compté (2026-11-04)

Mesure de départ (Xenoblade 2, 240 s : titre, ouverture, début de partie ; 1,66 million de dessins) : 0 dessin sauté, mais des compteurs mélangés (informations, textures de remplacement, état ignoré) et cinq états du jeu enregistrés puis jamais lus (`SetLineWidth`, `SetPointSize`, `SetDepthClamp`, `SetPrimitiveRestart`, `SetRasterizerDiscard`).

Ce qui est fait (patch moteur 0103 et `linux/host/`) :
- **Primitives** : ligne bouclée, bande de quadrilatères (index générés par `gpu.c`), polygone (éventail de triangles). Avant : dessin sauté, compté « primitive 2/8/9 ».
- **Tessellation sans étage de contrôle** : `passthrough_tcs` fabrique en SPIR-V l'étage que le matériel a quand un programme n'en a pas (sommets recopiés, niveaux de `SetOuter/InnerTessellationLevels`, taille de patch du jeu). Le dessin passe par Python (la voie C lui rend la main).
- **Copie texture vers tampon** (`nx2gpu_read_region`, avec les pas `copy_row_stride` / `copy_image_stride`) ; copie entre textures de formats différents mais de même taille de texel (copie brute).
- **État du jeu** appliqué : redémarrage de primitive (avec l'indice propre au jeu, remplacé par le tout-un de Vulkan), rejet du rasteriseur, écrêtage de profondeur, largeur de ligne. La taille de point reste non appliquée, comptée.
- **Textures finalisées** : `nvnTextureFinalize` ne fait plus disparaître la description d'une texture qu'un pool nomme encore (le matériel garde le descripteur) ; 150 « texture inconnue » sur le parcours deviennent 0.
- **Rapport** : `coverage_text()` (affiché à la fin de `horizon_run.py`) range chaque compteur : dessins sautés, textures de remplacement, état non appliqué, notes ; et liste les cas concrets (60 au plus).
- **Comparaison à une référence** : `framediff.py` (deux PNG ou deux dossiers : différence moyenne/maximale, part des pixels différents, PSNR, image de différences). Aucune référence n'est dans le dépôt. `NX2LINUX_NVN_REPLAY=check` compare les voies C et Python dessin par dessin.
- Les tests : 14 nouveaux dans `test_horizon_gpu.py` (primitives, redémarrage, rejet, écrêtage, largeur, tessellation avec niveaux lus par le shader, copie texture vers tampon, textures fantômes, rapport), `test_framediff.py`.

Mesure d'arrivée (même parcours, 280 s, 1,87 million de dessins, la voie C) : **0 dessin sauté, 0 état non appliqué, 151 dessins avec une texture de remplacement**, tous listés :
- 75 « un shader échantillonne en 2D (par son instruction) une texture 3D » (le shader `da21866f…`, emplacement 1) : la bibliothèque n'a pas de vue 2D d'une image 3D échantillonnable ; le traducteur garde le type que l'instruction nomme, donc l'apprentissage du type (`learn_type`) n'y change rien et le dit (`unlearnable`) ;
- 75 le même cas, vu du côté de la vue ;
- 1 identifiant de texture 0 (jamais enregistré dans le pool).
Notes : 131 textures de même mémoire mais de format ou de taille différents (des images distinctes, qui ne se voient pas : le plus souvent un flux de résolution de textures qui remplace l'autre au même endroit ; un cas de format 0x25 1280x720 contre 0x32 1024x1024 à la même adresse, à surveiller), 3 485 échantillonnages de stencil, 328 images partagées, 67 premiers usages vides.
Image vérifiée à la main : une scène du jeu (ciel, personnages, textures, sous-titres) s'affiche correctement.

Reste, par gravité :
1. La vue 2D d'une texture 3D (VK_EXT_image_2d_view_of_3d) n'est pas faite : 75 dessins pendant un chargement.
2. La taille de point (`SetPointSize`) n'est pas appliquée (comptée, 0 sur ce parcours).
3. Le redémarrage de primitive n'agit pas sur les listes (points, lignes, triangles) ni sur les primitives à index générés.
4. Une texture au-dessus de 96 Ko est jugée réécrite par un échantillon de sa mémoire jusqu'à ce qu'on l'ait vue réécrite (puis elle est hachée en entier) : une première réécriture partielle peut passer inaperçue.
5. Le mode `check` signale 0,9 % de dessins différents (même état, contenu d'un tampon différent) ; le même écart existait avant (premier écart à 13 s) : la mémoire du jeu change entre les deux passes. Non attribué plus précisément.
6. Un parcours plus long (combat, ville, carte) n'est pas fait : les chiffres ci-dessus valent pour le parcours testé.

