<script>
  import { isBlack, midiToName } from './notes.js';
  import { playNote } from './player.js';

  let { onnote, octave = $bindable(4) } = $props();
  const OCTAVES = 2;

  let keys = $derived.by(() => {
    const first = (octave + 1) * 12;
    const out = [];
    let white = 0;
    for (let midi = first; midi <= first + 12 * OCTAVES; midi++) {
      const black = isBlack(midi);
      out.push({ midi, black, white: black ? white - 1 : white });
      if (!black) white++;
    }
    return out;
  });
  let whites = $derived(keys.filter((k) => !k.black).length);
  let pressed = $state(null);

  function press(midi) {
    pressed = midi;
    playNote(midi);
    onnote(midi);
  }
</script>

<div class="piano-bar">
  <button type="button" onclick={() => (octave = Math.max(0, octave - 1))} disabled={octave <= 0}
    aria-label="Octave down">◀</button>
  <div class="piano" style="--whites: {whites}" role="group" aria-label="Piano keyboard">
    {#each keys as key (key.midi)}
      <button
        type="button"
        class:black={key.black}
        class:white={!key.black}
        class:pressed={pressed === key.midi}
        style="--i: {key.white}"
        title={midiToName(key.midi)}
        aria-label={midiToName(key.midi)}
        onpointerdown={(e) => { e.preventDefault(); press(key.midi); }}
        onpointerup={() => (pressed = null)}
        onpointerleave={() => (pressed = null)}
        onkeydown={(e) => (e.key === 'Enter' || e.key === ' ') && (e.preventDefault(), press(key.midi))}
      >
        {#if !key.black && key.midi % 12 === 0}<span>{midiToName(key.midi)}</span>{/if}
      </button>
    {/each}
  </div>
  <button type="button" onclick={() => (octave = Math.min(7, octave + 1))} disabled={octave >= 7}
    aria-label="Octave up">▶</button>
</div>

<style>
  .piano-bar {
    display: flex;
    gap: 6px;
    align-items: stretch;
  }
  .piano-bar > button {
    flex: none;
    width: 32px;
    border: 1px solid var(--border);
    border-radius: 6px;
    background: var(--surface);
    color: var(--text);
    cursor: pointer;
  }
  .piano-bar > button:disabled {
    opacity: 0.4;
    cursor: default;
  }
  .piano {
    position: relative;
    flex: 1;
    height: 120px;
    min-width: 0;
    user-select: none;
    touch-action: manipulation;
  }
  .piano button {
    position: absolute;
    top: 0;
    padding: 0;
    cursor: pointer;
    border: 1px solid #333;
  }
  .white {
    left: calc(var(--i) * 100% / var(--whites));
    width: calc(100% / var(--whites));
    height: 100%;
    background: #fdfdfb;
    border-radius: 0 0 5px 5px;
    z-index: 1;
  }
  .black {
    left: calc((var(--i) + 0.68) * 100% / var(--whites));
    width: calc(0.64 * 100% / var(--whites));
    height: 62%;
    background: #222;
    border-radius: 0 0 4px 4px;
    z-index: 2;
  }
  .white.pressed {
    background: #fed7aa;
  }
  .black.pressed {
    background: #c2410c;
  }
  .white span {
    position: absolute;
    bottom: 4px;
    left: 0;
    right: 0;
    font-size: 10px;
    color: #666;
    text-align: center;
  }
</style>
