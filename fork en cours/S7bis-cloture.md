# S7 bis — clôture : Xenoblade Chronicles 2 (0100E95004038000), état au 2026-11-03

Aucune donnée du jeu dans ce fichier. Les critères de sortie sont ceux de la Phase 5 de `PLAN_NX2Linux.md` (« Premier jeu validé »).

## Critères de sortie

| Critère | État | Preuve |
|---|---|---|
| Démarrage reproductible trois fois | **Atteint** | Plus d'une dizaine de lancements pendant la session, tous jusqu'à l'écran-titre ; avec appuis scriptés, tous jusqu'à l'ouverture, plusieurs jusqu'au premier combat |
| Menu et boucle de jeu atteints | **Atteint** | Écran-titre (« Press any button »), cinématique d'ouverture, pause/saut (Plus puis A), premier combat (Rex contre King Crustip) avec son interface, scène 3D éclairée et texturée |
| Clavier et manette utilisables | **Écrit, non confirmé à la main** | `horizon_input.py` : clavier et manette SDL vers l'état des manettes ; vérifié avec appuis scriptés (`NX2LINUX_PRESS`), pas avec un vrai clavier ou une vraie manette. Des appuis de 0,2 s sont perdus à 15 images/s, ceux de 1 s passent |
| Aucun `[RED]` non expliqué | **Atteint** | 0 ligne `[RED]` et 0 SVC non géré dans les derniers lancements. Seuls 2 appels « pas portés » (`nn::hid::SetNpadJoyHoldType`, `GetVibrationDeviceHandles`) sont journalisés, sans effet visible |
| Résultat documenté avec title ID, version, profil, GPU et pilote | **Atteint (ci-dessous)** | |

## Identification

| Élément | Valeur |
|---|---|
| Title ID | 0100E95004038000 |
| Version du jeu | 1.0.0 (`control.nacp`) ; build ID du module `main` : `5087af29…` |
| Profil | `horizon_profile_xc2` (applique le correctif propre au titre) |
| GPU / pilote | NVIDIA GeForce RTX 4060, pilote 615.78.08, Vulkan 1.4.351 (lavapipe aussi disponible) |
| Cadence mesurée | environ 15 images par seconde en moyenne depuis le démarrage (cible du jeu : 30) |

## Ce qui n'est pas fait (hors des critères, à connaître)

- **Audio** : le serveur audio est silencieux, aucun son n'est produit.
- **Sauvegardes** : non testées.
- **Cadence** : environ la moitié de la cible ; le goulot est la synchronisation entre threads qui passe par Python.
- **Vidéo** : `movie::Decoder` répond 0 ; aucune vidéo rencontrée jusqu'ici.
- **27 fonctions cryptographiques** non recompilables (AES, SHA, PMULL, `frsqrte`), jamais appelées jusqu'ici.
- **Rendu** : seuls le titre, l'ouverture et le premier combat ont été examinés.
- **Manifestes** : à refaire après le patch 0097 (172 cibles de tables de sauts de plus).

## Les défauts réglés pour y arriver (cette phase)

Enregistrement NVN en C (0089), file de rendu sur son thread (0090), segment c0 (0091), rejeu des dessins en C (0093), réveils ciblés entre threads (×4, 0094), accès mémoire en ligne (0095, optionnel), pile principale de 8 Mio et bornes des tables de sauts (0097), images partagées entre textures d'une même mémoire et échantillonnage du stencil (0098). Détails et mesures : `S8-recompilation-efficace.md`, sections 5 à 14.
