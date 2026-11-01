# Ce qui peut bloquer la suite (état au 2026-11-01, après T4)

Classé par gravité, avec la preuve de chacun. « Prouvé » = mesuré par un test ou une exécution ; « supposé » = pas encore vérifié.

## Bloquants pour T7 (un vrai module de jeu)

| # | Problème | Preuve | Piste |
|---|---|---|---|
| 1 | **RÉGLÉ le 2026-11-01 (voir bas de page).** ~~Une fonction non liftable empêche de construire tout appelant direct.** Si `f` fait `bl g` et que Remill ne sait pas lifter `g`, le lien échoue (`sub_<g>` indéfini) ; message actuel trompeur (« the runtime does not define »). | Prouvé : `f` seule → `LinkError … sub_2000` ; `f` + `g` (avec `svc`) → `LiftError`. | Fait : `Aot(..., stub_unliftable=True)`. |
| 2 | **RÉGLÉ le 2026-11-02 (voir bas de page).** ~~Les tables de sauts (`switch`) passent toutes par le JIT.~~ Chaque aiguillage = 1 entrée JIT ; en AOT seul, erreur explicite. | Prouvé (test_aot_realistic) : `switch` à 20 cas compilé par GCC → `br x2` vers le milieu de la fonction ; 20 aiguillages = 20 entrées JIT. | Lire la table au moment de la traduction et enregistrer chaque cible comme point d'entrée AOT supplémentaire (le fragment se termine par `ret` et reste correct), ou le transformer en `switch` local. Critère « JIT mesuré et limité » impossible tant que ce n'est pas fait. |
| 3 | **Trou de couverture FP/SIMD dans Remill.** Sur un échantillon C modeste compilé par GCC : 6 fonctions sur 20 ne se liftent pas ; 7 instructions sur 290 (2 %). | Prouvé : `fmul vN.4s, vN.4s, v0.s[0]`, `fsqrt`, `fcvtzs`, `cnt`, `str qN`, `stp qN, qM`, `str sN, [x, x]`. | Après le point 1, le JIT les exécute. Pour de bonnes performances : patcher Remill instruction par instruction, comme `adc`. Un jeu est très riche en flottants et vecteurs : la proportion réelle est inconnue. |
| 4 | **Exclusifs octet et demi-mot non décodés.** GCC (sans fonctions d'aide) émet `ldxrb`, `stxrb`, `ldaxrh`, `stlxrh`. | Prouvé (même test). | Même méthode que `stxr` (patch Remill) : variantes `B`/`H` de `LDXR`, `LDAXR`, `STXR`, `STLXR`, `LDAR`, `STLR`. |

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
