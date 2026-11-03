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
