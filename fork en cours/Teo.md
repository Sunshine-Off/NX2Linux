# NX2Linux — Partie Téo

## Partie lisible par les humains

### Responsabilité

Téo est responsable du moteur d’exécution et de la transformation du code ARM64
Switch vers du code x86_64 Linux.

### Travail à réaliser

1. Comprendre et documenter `patch_nso.py`, `nso.py`, `nso_code.py`, `nso_asm.py`,
   `nso2elf.py` et `nso_match.py`.
2. Définir la représentation intermédiaire utilisée par la recompilation.
3. Évaluer Remill et LLVM avant d’écrire un traducteur maison.
4. Construire une fixture ARM64 contrôlée.
5. Recompiler une fonction vers x86_64.
6. Ajouter la gestion des registres ARM64, NZCV, mémoire et atomiques.
7. Ajouter le JIT de secours pour les blocs inconnus ou dynamiques.
8. Définir l’état du CPU invité, le TLS et les threads.
9. Intercepter les SVC et les relier aux services Horizon.
10. Remplacer les dépendances Mach-O par un format de manifest ELF.

### Ordre des livraisons

- Jalon T1 : fixture ARM64 analysée
- Jalon T2 : fonction arithmétique recompilée
- Jalon T3 : branches, mémoire et appels
- Jalon T4 : JIT de secours
- Jalon T5 : SVC et état mémoire invité
- Jalon T6 : module ELF chargé dans le runtime
- Jalon T7 : premier module de jeu réel validé

### Critères de réussite

- le résultat AOT correspond à la référence ARM64
- les flags et accès mémoire sont corrects
- les branches indirectes sont sûres
- le fallback JIT est mesuré et limité
- les erreurs de traduction sont explicites
- les tests sont reproductibles sans données de jeu privées

## Partie destinée à l’IA

### Instructions de travail

- lire `engine/AGENTS.md` avant toute modification de l’engine
- ne jamais modifier directement la chaîne shader sans validation dédiée
- travailler dans `engine/`, puis exporter les changements vers `patches/`
- utiliser l’identité Git du fork si un commit est demandé
- ne jamais ajouter de fichier sous `dm/` à la racine du dépôt principal
- garder les fixtures synthétiques dans les tests
- documenter toute hypothèse sur les fonctions, les builds et les adresses
- préférer une erreur explicite à une traduction approximative

### Fichiers prioritaires

```text
engine/dm/tools/patch_nso.py
engine/dm/tools/nso.py
engine/dm/tools/nso_code.py
engine/dm/tools/nso_asm.py
engine/dm/tools/nso2elf.py
engine/dm/tools/nso_match.py
engine/dm/tools/sdk_patches.py
engine/dm/host/loader.c
engine/dm/host/hle_svc.c
engine/dm/host/thread.c
```

### Tests attendus

- tests unitaires des instructions et flags
- tests de branches directes et indirectes
- tests mémoire et atomiques
- tests de fonctions avec appels imbriqués
- tests de code dynamique via JIT
- tests SVC synthétiques
- tests de chargement ELF avec ASan et UBSan
- comparaison AOT, JIT et référence ARM64

### Règle de décision

Ne pas développer un émulateur complet. Utiliser une brique existante pour la levée,
la génération et le fallback. Le travail spécifique à NX2Linux est l’intégration
NSO, l’état invité, les services Horizon et le lien avec le host Linux.
