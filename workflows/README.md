# Workflows ComfyUI (format API)

Deposer ici les deux fichiers exportes depuis ComfyUI :

- `ltx25_i2v_api.json` : modele de ComfyUI **LTX-2.5 I2V** (image de depart)
- `ltx25_flf2v_api.json` : modele de ComfyUI **LTX-2.5 FLF2V** (image de depart + image de fin)

## Comment les obtenir

1. Ouvrir ComfyUI (a jour) : Workflow > Browse Templates > Video > LTX-2.5 I2V.
2. Renommer les noeuds (clic droit > Title) :
   - texte positif -> `INPUT_PROMPT`
   - texte negatif -> `INPUT_NEGATIVE` (optionnel)
   - Load Image -> `INPUT_IMAGE` (et `INPUT_END_IMAGE` pour l'image de fin dans FLF2V)
   - noeud qui porte width / height / length -> `INPUT_SIZE`
   - noeud qui porte le seed -> `INPUT_SEED`
   - noeud de sauvegarde video -> `OUTPUT_VIDEO`
3. Workflow > **Export (API)**. Ne pas utiliser l'export normal : le worker refuse le format interface.

Sans ces titres, le worker essaie de deviner les noeuds, mais les titres rendent le
resultat fiable.
