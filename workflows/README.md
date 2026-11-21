# Workflows ComfyUI (format API)

| Fichier | Etat | Usage |
| --- | --- | --- |
| `ltx25_i2v_api.json` | pret | image de depart -> video (modele officiel ComfyUI "LTX-2.5 I2V") |
| `ltx25_flf2v_api.json` | a ajouter | image de depart + image de fin -> video ("LTX-2.5 FLF2V") |

Les titres de controle sont poses directement dans le JSON (`_meta.title`) :

| Titre | Noeud dans ltx25_i2v_api.json | Champ |
| --- | --- | --- |
| INPUT_PROMPT | 398:376 Prompt | value |
| INPUT_NEGATIVE | 398:373 CLIP Text Encode | text |
| INPUT_IMAGE | 395 Load First Frame | image |
| INPUT_WIDTH | 398:372 Width | value |
| INPUT_HEIGHT | 398:360 Height | value |
| INPUT_SECONDS | 398:362 Duration | value |
| INPUT_FPS | 398:361 Frame Rate | value |
| INPUT_SEED | 398:339 RandomNoise (1re passe) | noise_seed |
| OUTPUT_VIDEO | 75 Save Video | - |

Pour ajouter un nouveau workflow : l'exporter depuis ComfyUI avec File > Export (API),
puis poser ces titres sur les noeuds correspondants (dans ComfyUI ou directement dans le JSON).
L'ameliorateur de prompt du workflow officiel reste desactive (noeud 398:383 = false).
