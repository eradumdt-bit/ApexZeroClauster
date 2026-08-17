# APEX ZERO — compteur sur materiel reel

Ce lot contient les deux nouveaux fichiers pour faire tourner le compteur
sur du vrai materiel, en plus de `Dashboard_simulator.py` (inchange,
garde comme banc de test PC) et `apex_zero_button_box.ino` (inchange,
deja fonctionnel) :

- `apex_zero_dashboard_pi.py` — tourne sur le Raspberry Pi, pilote les
  3 ecrans SPI + le moteur pas a pas, lit la telemetrie (UART) et les
  boutons (USB).
- `apex_zero_can_translator.ino` — tourne sur un **Arduino Mega**, lit
  les signaux bruts du connecteur d'origine de la voiture et envoie la
  trame telemetrie au Pi via Serial1 (TX1/RX1).

Toutes les instructions de cablage detaillees sont dans les commentaires
en tete de chaque fichier — ce README ne fait que donner la vue
d'ensemble et l'ordre des etapes.

## Ce que ca branche, avec ta liste de courses

| Piece                              | Role                    | Cote          |
|-------------------------------------|--------------------------|--------------|
| Raspberry Pi 4 (1 Go)               | cerveau de l'affichage    | -            |
| ILI9341 2.4" SPI                    | ecran "CRT" (jauges)      | Pi, SPI0     |
| GC9A01 rond x2                      | vitesse digitale + horloge| Pi, SPI0     |
| 28BYJ-48 + ULN2003 (x1 des 8)       | aiguille de vitesse       | Pi, GPIO     |
| Arduino (boitier de boutons)        | 5 boutons + OLED          | Pi, **USB**  |
| Arduino Mega 2560                   | traduction signaux voiture| Pi, **UART** |

Le lot de 8 moteurs 28BYJ-48/ULN2003 que tu as achete : un seul est
utilise ici (l'aiguille de vitesse). Les 7 autres sont dispos si tu veux
plus tard motoriser d'autres aiguilles (temp, jauge essence...) plutot
que de les afficher en digital sur le rond central — le code est
structurable pour ca le moment venu, mais ce n'est pas fait dans cette
version.

## Ordre de mise en route conseille

1. **Cablage SPI + moteur (Pi seul, sans le Mega pour l'instant)**
   Suivre le schema en tete de `apex_zero_dashboard_pi.py`. Installer les
   dependances (`pip3 install ...`, voir meme fichier), puis lancer en
   mode debug pour verifier que les 3 ecrans s'allument et que le moteur
   tourne, sans se soucier des vraies donnees.

2. **Boitier de boutons en USB**
   Rien a changer, il fonctionne deja tel quel — juste identifier son
   port (`ls /dev/tty*` avant/apres branchement) et le passer en
   `--buttons-port`.

3. **UART Pi <-> Mega**
   C'est l'etape la plus delicate : le Mega est en logique 5V, le Pi en
   3.3V non tolerant. Le diviseur de tension sur la ligne Mega-TX1 ->
   Pi-RXD est **obligatoire**, pas optionnel. Voir le schema exact en
   tete de `apex_zero_dashboard_pi.py` (section 3). Activer l'UART
   materiel via `raspi-config` avant de tester.

4. **Flasher le Mega avec `apex_zero_can_translator.ino`**
   Le sketch tourne et envoie une trame meme sans aucun capteur branche
   (les entrees analogiques flottantes donneront juste des valeurs
   incoherentes) — utile pour verifier la liaison serie avant de cabler
   la voiture.

5. **Cablage voiture -> Mega, capteur par capteur**
   A faire un capteur a la fois, en verifiant chaque valeur affichee
   contre une mesure independante (multimetre, ou jauge d'origine encore
   en place le temps du recalage). Voir la liste "A calibrer" ci-dessous.

## A calibrer avant de faire confiance a l'affichage

Tout ce qui suit est un point de depart plausible dans le code, **pas**
une valeur mesuree sur ta 190E :

- `RPM_PULSES_PER_REV` (regime) — nombre d'impulsions par tour reellement
  fourni par le negatif bobine du M104.
- `SPD_PULSES_PER_KM` (vitesse) — a etalonner en roulant une distance
  connue.
- `TEMP_CURVE`, `FUEL_CURVE` (courbes des sondes resistives d'origine)
  — remplacer par les vraies courbes VDO/Bosch une fois les references
  exactes des capteurs identifiees.
- Echelles des capteurs de pression (`readPressureBar(..., maxBar)`)
  pour turbo/huile/eau.
- Table de tensions du selecteur de vitesse (`readGear()`).
- La consommation instantanee (`estimateConso`) est une estimation
  grossiere en attendant une vraie mesure sur les injecteurs — a
  remplacer en priorite si l'ordinateur de bord doit etre fiable.
- Le circuit de mise en forme du signal regime (bobine -> Mega) n'est
  pas dans le code : c'est un montage electronique externe (clamp +
  mise en forme) a realiser avant de brancher quoi que ce soit sur la
  broche RPM du Mega, pour ne pas y envoyer une haute tension.

## Format de trame

Le format ($RPM:...,...*XX) est identique dans les trois fichiers
(simulateur, script Pi, sketch Mega) et n'a pas ete modifie — c'est ce
qui permet de garder `Dashboard_simulator.py` comme banc de test valide
meme une fois le materiel reel en place.
