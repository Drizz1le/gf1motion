<p align="center">
  <img src="images/poc.gif" alt="Proof of concept — Bulbasaur playing a modified idle animation in Citra" width="720">
</p>

<p align="center">
  <strong>A byte-level atlas of the Pokémon X/Y & ORAS animation format that allows for custom animations</strong>
</p>

<p align="center">
  Full interactive atlas: <a href="https://drizz1le.github.io/gf1motion">drizz1le.github.io/gf1motion</a>
</p>

<p align="center">
  <a href="#how">How</a> ·
  <a href="#proof-of-concept">Proof of Concept</a> ·
  <a href="#goal">Goal</a> ·
  <a href="#credits">Credits</a> ·
  <a href="#license">License</a>
</p>

---

# Tools coming soon

<a id="how"></a>
## How

No public specs existed for this format so it came down to classic reverse engineering by diffing them byte-by-byte in HxD, and repeatedly asking myself the designer's question:
*if I had to lay this data out myself, how would I do it?*

SPICA's source code provided a head start, but only half of one. SPICA can play back these
animations, but the renderer is allowed to skip anything it doesn't understand. Rebuilding a file
the game will actually accept allows no such shortcuts. Every count, offset, alignment pad, and
size field has to be written back exactly. Closing that gap, from partial decoder to full
encoder, was the real work. The result is verified by byte-exact round-trips.

The complete byte layout which shows the container, motion pack, skeleton, and the 3-bit channel encoding, 
is documented region-by-region in the [interactive atlas](https://drizz1le.github.io/gf1motion),
with every example drawn from real file bytes.

<a id="proof-of-concept"></a>
## Proof of Concept

Decoding a format convinces you. Re-encoding it convinces the game. To prove the format was
fully solved, I exported Bulbasaur's idle clip to JSON and made one small edit: the frame-21
key of **Waist · TranslationX**, from `0.4954` to `6.28318`. This is an 2π-unit displacement chosen to be
impossible to miss.

<table>
  <thead>
    <tr>
      <th width="50%">Before</th>
      <th width="50%">After</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td>
<pre><code>"bones": [
    {
      "name": "Waist",
      "channels": {
        "TranslationX": [
          {
            "frame": 0,
            "value": -0.4999988,
            "slope": 0.3582439
          },
          {
            "frame": 21,
            "value": 0.495449,
            "slope": 0.0
          },
          {
            "frame": 42,
            "value": -0.4999988,
            "slope": -0.1976028
          }
        ], ...</code></pre>
      </td>
      <td>
<pre><code>"bones": [
    {
      "name": "Waist",
      "channels": {
        "TranslationX": [
          {
            "frame": 0,
            "value": -0.4999988,
            "slope": 0.3582439
          },
          {
            "frame": 21,
            "value": 6.28318,
            "slope": 0.0
          },
          {
            "frame": 42,
            "value": -0.4999988,
            "slope": -0.1976028
          }
        ], ...</code></pre>
      </td>
    </tr>
  </tbody>
</table>

The edited clip was re-encoded, patched into the `.PB`, and repacked into the GARC with garctool.
Loaded in Citra, the modified animation plays flawlessly. That's the clip at the top of this
page. 

<a id="goal"></a>
## Goal

This project exists because the older games animated with more soul. Pokémon Battle Revolution's
battle animations are expressive and full of character; the 3DS era's are fine at best.
I've always loved difficulty hacks for the gen-6 games, but the animations bothered me every
time.

The end goal: port Battle Revolution's animations onto the 3DS games' skeletons. With the format
now fully writable, it's just a retargeting problem.

<a id="credits"></a>
## Credits

- **[SPICA](https://github.com/gdkchan/SPICA)** (and the Wambosa fork) — open-source 3DS model
  viewer.
- **HxD** — Hex editor
- **garctool** — GARC extraction and rebuild.
- **Citra** — 3DS emulation for testing.
- Reverse engineering — **Ben Kudarauskas**
  ([@drizz1le](https://github.com/drizz1le)). Documentation built with AI assistance
  (Claude)

<a id="license"></a>
## License

[MIT](LICENSE) — covers the tooling and documentation in this repository.

The small byte excerpts shown in the atlas are from Pokémon X/Y and remain
© Nintendo / Game Freak / Creatures Inc., reproduced solely for interoperability documentation.
**No game files or assets are distributed here** — extract them from your own legally obtained
copy.
