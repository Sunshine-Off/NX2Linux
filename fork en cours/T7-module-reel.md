# T7 — premier module réel : Balatro (LÖVE sur SDK Nintendo), 2026-11-01

Données : le NSP de Téo (non versionné : `.git/info/exclude`), extrait avec `nx_extract` (dotnet 10 installé sans sudo dans le dossier temporaire) et `prod.keys`, dans le dossier temporaire. Rien du jeu n'est dans le dépôt ni dans ce document : seulement des adresses, des nombres et des noms de mnémoniques.

## Définitions (analogies)

- **Couverture** : sur 100 pages d'un livre, combien le traducteur sait traduire entièrement. Une seule phrase intraduisible dans une page suffit à la compter comme non traduite (elle passe alors par le JIT).
- **Test différentiel** : deux traducteurs indépendants (Unicorn, un émulateur ARM64 existant, et notre code recompilé) lisent la même page avec les mêmes entrées ; si leurs réponses (registres, drapeaux, mémoire) diffèrent d'un bit, c'est un bogue chez nous.

## Les modules

| Module | Fonctions | Dont sans entrée de déroulement (PLT, appelées) | Relocations | Imports non définis |
|---|---|---|---|---|
| rtld | 34 | 1 | 36 | 18 |
| main | 8 099 | 400 | 11 509 | 461 |
| sdk | 40 326 | 8 903 | 35 127 | 13 |
| subsdk0 (le jeu et LÖVE) | 60 411 | 2 230 | 108 567 | 371 |
| subsdk1 | 2 012 | 193 | 1 673 | 68 |

Conversion NSO -> ELF + manifeste : 14 s pour les cinq modules (`elf_module.py`).

## Résultats

| Mesure | Résultat |
|---|---|
| Couverture de lifting, échantillon de 1 500 fonctions de subsdk0 | **88,6 %** se liftent entièrement (1 329 / 1 500) |
| Test différentiel, 400 fonctions « feuilles » de subsdk0 (sans appel), 16 entrées aléatoires chacune | **0 différence.** 280 fonctions identiques à Unicorn sur **2 658 cas** (registres, NZCV, mémoire inscriptible), 78 sans cas exploitable (tous les cas : faute des deux côtés), 42 non liftables |
| Les mêmes fonctions non liftables, par le JIT (échantillon de 25) | 13 fonctions identiques (33 cas), 0 différence, 12 sans cas exploitable |
| Modules entiers construits (lifting de toutes les fonctions, compilation, édition de liens, JIT prêt) | rtld 3 s ; subsdk1 (2 012 fonctions) 19 s ; main (8 099) 202 s, 1 142 fonctions au JIT (14 %) ; **subsdk0 (60 411) 34 min 35 s sur 14 cœurs, 7 382 fonctions au JIT (12,2 %)** |
| Débit de lifting | environ 0,6 s par fonction et par cœur (remill-lift + clang) ; ~416 minutes de CPU pour subsdk0 |

## Ce que T7 a trouvé de faux ou de manquant (tous corrigés ici)

1. **Le PLT des vrais modules a une seule entrée de déroulement pour toute la table**, chaque stub étant appelé séparément : la construction de subsdk1 échouait (`sub_8041290` indéfini, etc.). Un appel direct vers l'intérieur d'une autre fonction coupe maintenant cette fonction à la cible (H31). Après coupure, les entrées « call » de subsdk1 égalent ses relocations JUMP_SLOT (193 = 193).
2. **Une fonction de plus de 64 Ko arrêtait la construction de tout `main`** : `remill-lift` reçoit le code en un seul argument hexadécimal et Linux limite un argument à 128 Ko (`Argument list too long`). C'est maintenant une `LiftError` explicite, et la fonction passe par le JIT (H35).
3. La découverte des fonctions de `elf_module.py` était quadratique : 30 s et plus pour le module sdk, maintenant moins de 5 s.
4. Le lifting et la compilation étaient séquentiels : 124 s pour subsdk1 avant, 19 s après (pool de threads, un clang par fonction).

## Pourquoi les 12 % de fonctions au JIT

Premier mnémonique intraduisible, sur l'échantillon de 1 500 fonctions : `stp`/`str`/`ldp` de registres flottants (prologues qui sauvent d8–d15, plus de la moitié), `dsb`, `scvtf`, `fcsel`, `fmin`, `fcvtms`, `fmla`, `fcvtn`, `ushll`, `ld3`/`st4`. Ce sont les blocages 3 de `blocages-connus.md` : le lot le plus rentable est « registres flottants en paire ou en pré-indexé » et `dsb`, qui à eux seuls expliquent la majorité.

## Honnêtement : ce que T7 ne prouve pas

- **Le jeu n'est pas lancé.** Aucune séquence de démarrage, aucun appel de service réel, aucune image. « Premier module réel validé » veut dire : lu, converti, relocalisé, entièrement construit, et des centaines de fonctions réelles identiques à Unicorn. Pas « Balatro tourne ».
- **Les feuilles ne représentent que 13 % des fonctions** et excluent exactement les appels, les sauts indirects et les `svc` (H32). Les fonctions avec appels sont construites mais pas comparées.
- **53 % des cas aléatoires fautent des deux côtés** (H33) : le résultat repose sur les 2 658 cas égaux, pas sur le total.
- **FP/SIMD : peu testé** (H34) : les registres vectoriels ne sont pas fixés par `Aot.run`, donc comparés à zéro des deux côtés.
- Aucune mesure de vitesse d'exécution, seulement du coût de traduction (le débit ci-dessus : 35 minutes, c'est trop pour du démarrage ; il faudra un cache et des fonctions traduites à la demande).
- Les 461 imports de `main` et les 371 de subsdk0 ne sont reliés à rien : appeler l'un d'eux s'arrête avec son nom (H30). Les relier aux vraies fonctions HLE est le travail de l'hôte (H29).

## Où continuer

1. Patcher Remill pour les paires/pré-indexés flottants, `dsb`, `scvtf`, `fcsel` : environ deux tiers de la part JIT.
2. Cache de traduction par module (empreinte du module -> objets) et traduction à la demande : 35 minutes à chaque lancement n'est pas acceptable.
3. Un test différentiel avec appels : fournir les callees des deux côtés (les modules entiers se construisent déjà).
4. Brancher `svc_dispatch` et les premières fonctions HLE d'`hle.c` aux imports.
