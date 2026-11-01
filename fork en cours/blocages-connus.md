# Ce qui peut bloquer la suite (état au 2026-11-01, après T4)

Classé par gravité, avec la preuve de chacun. « Prouvé » = mesuré par un test ou une exécution ; « supposé » = pas encore vérifié.

## Bloquants pour T7 (un vrai module de jeu)

| # | Problème | Preuve | Piste |
|---|---|---|---|
| 1 | **RÉGLÉ le 2026-11-01 (voir bas de page).** ~~Une fonction non liftable empêche de construire tout appelant direct.** Si `f` fait `bl g` et que Remill ne sait pas lifter `g`, le lien échoue (`sub_<g>` indéfini) ; message actuel trompeur (« the runtime does not define »). | Prouvé : `f` seule → `LinkError … sub_2000` ; `f` + `g` (avec `svc`) → `LiftError`. | Fait : `Aot(..., stub_unliftable=True)`. |
| 2 | **RÉGLÉ le 2026-11-02 (voir bas de page).** ~~Les tables de sauts (`switch`) passent toutes par le JIT.~~ Chaque aiguillage = 1 entrée JIT ; en AOT seul, erreur explicite. | Prouvé (test_aot_realistic) : `switch` à 20 cas compilé par GCC → `br x2` vers le milieu de la fonction ; 20 aiguillages = 20 entrées JIT. | Lire la table au moment de la traduction et enregistrer chaque cible comme point d'entrée AOT supplémentaire (le fragment se termine par `ret` et reste correct), ou le transformer en `switch` local. Critère « JIT mesuré et limité » impossible tant que ce n'est pas fait. |
| 3 | **PARTIELLEMENT RÉGLÉ le 2026-11-02 (scalaire et mémoire faits, NEON restant : voir bas de page).** Trou de couverture FP/SIMD dans Remill. Sur un échantillon C modeste compilé par GCC : 6 fonctions sur 20 ne se liftent pas ; 7 instructions sur 290 (2 %). | Prouvé : `fmul vN.4s, vN.4s, v0.s[0]`, `fsqrt`, `fcvtzs`, `cnt`, `str qN`, `stp qN, qM`, `str sN, [x, x]`. | Après le point 1, le JIT les exécute. Pour de bonnes performances : patcher Remill instruction par instruction, comme `adc`. Un jeu est très riche en flottants et vecteurs : la proportion réelle est inconnue. |
| 4 | **RÉGLÉ le 2026-11-02 (voir bas de page).** ~~Exclusifs octet et demi-mot non décodés.~~ GCC (sans fonctions d'aide) émet `ldxrb`, `stxrb`, `ldaxrh`, `stlxrh`. | Prouvé (même test). | Même méthode que `stxr` (patch Remill) : variantes `B`/`H` de `LDXR`, `LDAXR`, `STXR`, `STLXR`, `LDAR`, `STLR`. |

## Gênants, pas bloquants

| # | Problème | Statut |
|---|---|---|
| 5 | Le cache du JIT est par thread et une écriture n'invalide que le cache du thread qui écrit (H15). | Connu, à traiter en T5 (threads). |
| 6 | `ret` ignore la valeur de x30 (H7/H14) : un changement de contexte ou un `longjmp` qui réécrit x30 serait mal traduit. | Supposé : à chercher dans un vrai module. |
| 7 | Les outils (`arm64_survey`) n'ont vu que la fixture : pas de module compilé par un vrai compilateur, pas de MOD0/`eh_frame` réel (H1 : constantes dans les fonctions ?). | Non testé. |
| 8 | Aucune mesure fine de performance (état en mémoire, 1re entrée JIT 20 à 54 ms). | Non mesuré. |
| 9 | `ccmn` en forme registre et LSE restent des `LiftError` (la Switch n'a pas LSE). | Connu. |

## Fragilité de l'environnement (pas des bugs du code)

- **Remill (patché) et dynarmic sont compilés dans le dossier temporaire du scratchpad.** Les patches Remill sont dans `remill-patches/` ; dynarmic est un clone non modifié d'`azahar-emu/dynarmic` @ `a466015`. Si le dossier disparaît : tout recompiler (≈ 10 min).
- **Rien n'est poussé** : les 4 branches (`feat/teo-recompilation-arm64`, `…t3…`, `…t4…`) n'existent qu'en local (la première seulement jusqu'à T2 sur le distant).
- **Un test du dépôt échoue avec ta locale française** (`tools/tests/test_patches.py`, il attend « Patch failed » en anglais) : sans rapport avec ce travail ; passe avec `LC_ALL=C`.

## Défauts de mes propres tests trouvés en route (tous corrigés)

Comparaisons « vides » qui passaient sans rien vérifier (référence qui échoue comme l'AOT ; `inc` jamais comparé car Unicorn échouait sur les globales non mappées) ; constantes de registres Unicorn ; mémoire AOT non remise à zéro ; message du lieur non reconnu en français ; adresse de retour factice dans x30. Tout test de comparaison refuse maintenant qu'une exécution de référence échoue sans que ce soit attendu.

## Blocage 1 réglé (2026-11-01)

`Aot(functions, stub_unliftable=True)` : une fonction que Remill ne sait pas lifter, ou dont le code appelle un symbole que le runtime ne définit pas, devient un **stub** `sub_<adresse>` (fichier généré `stubs<n>.cpp`, fonction `aot_unlifted` dans `aot_runtime.cpp`). Analogie : une déviation balisée sur une route coupée, au lieu d'un pont qui manque et qui empêche de construire toute la route.

```
bl g  --(lien)-->  sub_g (stub)  --> aot_unlifted  -- JIT présent -->  dynarmic exécute g (compté, journalisé)
                                                   -- pas de JIT   -->  erreur : adresse + raison (instruction fautive)
```

- Par défaut rien ne change : `LiftError` / `LinkError` explicites. Le stub est un choix de l'appelant.
- Le stub n'est pas dans la table AOT : un appel indirect vers cette fonction passe par le JIT comme toute cible inconnue (pas de cas particulier).
- Une fonction appelée mais **jamais fournie** (`sub_X` indéfini) reste une `LinkError` même en mode stub : c'est une entrée manquante, pas une limite de Remill.
- Compteur `unlifted_calls`. `Aot.unlifted` donne `{adresse: raison}`.
- Tests : `tests/test_aot_stub.py` (8 tests, dont l'égalité avec Unicorn sur 36 cas via le JIT).

**Trouvé en route (nouveau, pas encore réglé)** : `fadd`, `fmul`, `fdiv` se liftent mais leur IR appelle `__remill_fpu_exception_clear/test`, que le runtime ne définit pas. Avant ce correctif, tout code flottant arithmétique échouait donc au lien. Désormais ces fonctions passent par le JIT (correct, mais plus lent). Les définir dans le runtime (AOT natif) est à faire avec le blocage 3.

## Blocage 2 réglé (2026-11-02) : les tables de sauts sont résolues à la traduction

`jump_tables.py` repère chaque `br` qui est un `switch` et en lit la table. Analogie : au lieu de laisser le voyageur arrivé au carrefour demander son chemin à un guide (le JIT) à chaque passage, on a lu le panneau une fois pour toutes et préparé une route par destination.

```
adrp x9,T ; add x9,x9,#lo ; adr x10,BASE ; ldrb w11,[x9,x8] ; add x10,x10,x11,lsl #2 ; br x10
      \__ une exécution symbolique des (jusqu'à 400) instructions avant le br : quels registres valent une adresse connue, lequel est une entrée de table
cmp w8,#N ; b.hi défaut   -> N+1 entrées (une de marge) ; sans garde : on lit tant que la cible reste dans la fonction
chaque cible = une entrée AOT (sub_<cible>) liftée DEPUIS son adresse, avec toute la fonction autour (--entry_address de remill-lift), pour que les sauts en arrière trouvent leur code
br x10 -> __remill_jump -> aot_find(cible) : un appel terminal vers le cas, sans JIT
```

Formes reconnues : LLVM (octet ou demi-mot, `add ..., lsl #2`), GCC (octet signé, `add x3, x3, w1, sxtb #2`), mot relatif à la table (`ldrsw` puis `add`). Un `br` non reconnu reste un saut indirect (pointeur de fonction, appel terminal).

- Sur Balatro : 742 tables dans `main` (6 884 cas), 3 138 dans `subsdk0` (32 481 cas), 642 dans `sdk` (6 317 cas) ; le manifeste liste les cas sous `jump_targets`.
- Test GCC (`test_aot_realistic`) : le `switch` à 20 cas qui coûtait 20 entrées JIT en coûte **0**, et l'AOT seul n'a plus d'erreur. Sans les tables : toujours l'erreur explicite d'avant (testé aussi).
- Test différentiel sur de vraies fonctions de Balatro contenant un `switch` : 78 fonctions identiques à Unicorn (1 152 cas, **0 différence**), **2 243 sauts indirects pris, 0 demande de repli** (ni JIT ni erreur).
- Tests : `test_jump_tables.py` (8, code assemblé à la main : octet, demi-mot, GCC signé, mot relatif, avec et sans garde, cibles hors fonction, `br` qui n'est pas une table), `test_elf_module.py::Switch` (un module synthétique avec un `switch` LLVM, égal à Unicorn).

Coût et limites :
- **Plus d'entrées à traduire** : +6 884 / +32 481 / +6 317 entrées (environ +50 % de lifts pour `subsdk0`, 60 411 -> 92 892 entrées). Chaque cas relifte la fin de sa fonction : du code dupliqué, correct mais coûteux en temps de traduction (le cache en absorbe le prix une seule fois).
- Une table dont l'adresse est calculée autrement (registre rempli par un appel, deux niveaux d'indirection) n'est pas vue : le saut reste indirect et passe par le JIT, comme avant. Parmi les `br` de Balatro qui ressemblaient à des tables, seuls des motifs sans table restent non reconnus (à vérifier plus finement).
- Une valeur périmée dans l'exécution symbolique (le registre dépend d'une branche) fait au pire manquer des cibles (JIT), jamais produire un résultat faux : un mauvais cas en trop n'est jamais atteint.

## Blocages 3 et 4 (2026-11-02) : les flottants scalaires, les accès mémoire vectoriels et les exclusifs octet/demi-mot

Mesuré sur les quatre modules de Balatro (`module_validate.py census` : chaque instruction distincte, registres masqués, est liftée une fois ; 7 256 380 mots) :

| | Avant | Après |
|---|---|---|
| Instructions que Remill ne lift pas | 66 047 (0,91 %), 964 classes | **39 811 (0,55 %), 625 classes** |
| Fonctions de `subsdk0` entièrement liftables (échantillon de 1 500) | 88,6 % | **97,4 %** |
| Entrées laissées au JIT, `main` | 1 142 sur 8 099 (14,1 %) | **524 sur 14 898 (3,5 %)** (les cas de `switch` comptent maintenant comme entrées) |
| Entrées laissées au JIT, `subsdk1` | 12 sur 2 012 | **3 sur 2 054** |
| Fonctions réelles sans appel contenant un `switch`/flottants, comparées à Unicorn avec des registres vectoriels aléatoires | (registres vectoriels à zéro des deux côtés) | **197 identiques, 1 802 cas, 0 différence** ; non liftables 7 % (10,5 % avant) |

Ce qui a été fait (Remill patch 0004, `remill-patches/`, et `aot_runtime.cpp`) :
- **Un défaut plus gros que prévu** : toute l'arithmétique flottante (`fadd`, `fsub`, `fmul`, `fdiv`, `fmadd`, `scvtf`, `fcvtz*`) se liftait mais ne se **liait** pas : le runtime ne définissait pas `__remill_fpu_exception_clear/test`. Ces fonctions étaient donc envoyées au JIT. Définis (par `fetestexcept`), ils font passer tout cela en AOT natif.
- **Nombres flottants conformes à ARM** : `fcvtz*` saturait mal (x86 rend 0x80000000 pour tout ce qui dépasse, ARM rend INT_MAX/INT_MIN/0/UINT_MAX et lève Invalid) ; une opération invalide sans NaN en entrée donne le NaN par défaut positif d'ARM, pas le négatif de x86.
- **Instructions ajoutées** : FCSEL, FCCMP/FCCMPE, FMIN/FMAX/FMINNM/FMAXNM, FNMUL, FMSUB/FNMADD/FNMSUB, FSQRT, FRINT{N,A,M,P,Z,X,I}, FCVT{N,A,M,P}{S,U}, FCVTZ{S,U} (et leurs formes à virgule fixe), SCVTF/UCVTF (virgule fixe, forme scalaire SIMD) ; STR de registres vectoriels B/H/S/D/Q en pré/post-indexé et à registre, STP de S/D/Q (Q = deux écritures de 128 bits) ; **blocage 4** : LDXRB/H, LDAXRB/H, STXRB/H, STLXRB/H, STLRB/H, plus CLREX (1 065 fonctions de Balatro), DSB, PRFM/PRFUM (sans effet).
- `Aot.run` donne et rend maintenant les registres vectoriels, FPCR et FPSR (les drapeaux cumulés d'exception sont dans `State.sr` : ils sont recomposés).
- Tests : `test_aot_simd.py` (assemble chaque instruction avec `aarch64-linux-gnu-as`, la lifte seule, la compare à Unicorn sur 24 entrées de registres vectoriels aux valeurs limites ; 48 instructions mémoire, 175 flottantes, 54 d'exclusifs et de barrières, plus un test qui fige les écarts de drapeaux FPSR connus).

Ce qui reste du blocage 3, par ordre d'effet sur Balatro (nombre de fonctions) : NEON `fmul`/`fmla` par élément (455 / 214), `scvtf`/`ucvtf` vectoriels (289), `st2`/`st4`/`ld1`-`ld4` par voie, `mov v.d[1], v.d[0]` (244), `shl`/`ushll`/`ushr`/`ushl` (≈ 180 chacune), `uzp`/`zip`/`trn`, `xtn`, `dup` élément, `cmhi`/`cmhs`, `rev64`, `sqrdmulh`, `cnt`, `clz`, `fcvtn`/`fcvtl`, plus le `ccmn` en forme registre. Ce sont des centaines de formes (7 arrangements × variantes) ; elles restent au JIT, qui les exécute correctement (testé par les tests de T4 et les comparaisons réelles ci-dessus).

Écarts connus (testés, figés) : le drapeau Inexact des conversions entier -> flottant (`scvtf`) et le Underflow de `fmadd` (contournement propre à Remill) diffèrent d'Unicorn ; les NaN (priorité entre deux NaN, charge utile) ne sont pas ceux d'ARM sauf le NaN par défaut.
