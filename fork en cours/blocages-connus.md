# Ce qui peut bloquer la suite (état au 2026-11-01, après T4)

Classé par gravité, avec la preuve de chacun. « Prouvé » = mesuré par un test ou une exécution ; « supposé » = pas encore vérifié.

## Bloquants pour T7 (un vrai module de jeu)

| # | Problème | Preuve | Piste |
|---|---|---|---|
| 1 | **Une fonction non liftable empêche de construire tout appelant direct.** Si `f` fait `bl g` et que Remill ne sait pas lifter `g`, le lien échoue (`sub_<g>` indéfini) ; message actuel trompeur (« the runtime does not define »). | Prouvé : `f` seule → `LinkError … sub_2000` ; `f` + `g` (avec `svc`) → `LiftError`. | Pour chaque fonction non liftable, générer un **stub `sub_<adresse>` qui appelle le JIT** (compté, journalisé). Petit, et il rend tout module constructible. À faire en premier. |
| 2 | **Les tables de sauts (`switch`) passent toutes par le JIT.** Chaque aiguillage = 1 entrée JIT ; en AOT seul, erreur explicite. | Prouvé (test_aot_realistic) : `switch` à 20 cas compilé par GCC → `br x2` vers le milieu de la fonction ; 20 aiguillages = 20 entrées JIT. | Lire la table au moment de la traduction et enregistrer chaque cible comme point d'entrée AOT supplémentaire (le fragment se termine par `ret` et reste correct), ou le transformer en `switch` local. Critère « JIT mesuré et limité » impossible tant que ce n'est pas fait. |
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
