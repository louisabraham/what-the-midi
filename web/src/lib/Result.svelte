<script>
  import * as api from './api.js';
  import { formatTime, splitFile } from './notes.js';
  import { playNotes, stop } from './player.js';
  import Score from './Score.svelte';

  let { result } = $props();

  // the occurrence on screen; a click on another time loads its notes
  let view = $derived({
    notes: result.notes, start: result.start, match: result.match, ppq: result.ppq,
  });
  let name = $derived(splitFile(result.file));
  let playing = $state(false);
  let timer;

  async function show(position) {
    view = { ...(await api.context({ doc: result.doc, pos: position.pos, len: view.match.len })) };
  }

  function play() {
    clearTimeout(timer);
    if (playing) {
      stop();
      playing = false;
      return;
    }
    playing = true;
    timer = setTimeout(() => (playing = false), playNotes(view.notes) * 1000);
  }
</script>

<article class="card">
  <header>
    <div class="name">
      <h3>{name.title}</h3>
      {#if result.title && result.title !== name.title}<p class="embedded" title="Title written in the MIDI file">“{result.title}”</p>{/if}
      <p>
        {#if name.folder}{name.folder}{/if}
        {#if result.source}<span class="tag" title="Collection">{result.source}</span>{/if}
        {#if result.copies > 1}<span class="tag" title="Number of files with this melody">{result.copies} copies</span>{/if}
      </p>
    </div>
    <button type="button" class="play" onclick={play} aria-label={playing ? 'Stop' : 'Play'}>
      {playing ? '■' : '▶'}
    </button>
  </header>
  <Score notes={view.notes} ppq={view.ppq} from={view.match.pos - view.start}
    to={view.match.pos - view.start + view.match.len} />
  <footer>
    <span>{result.occurrences} {result.occurrences === 1 ? 'match' : 'matches'} at</span>
    {#each result.positions as position (position.pos)}
      <button type="button" class="time" class:current={position.pos === view.match.pos}
        onclick={() => show(position)}>{formatTime(position.sec)}</button>
    {/each}
    {#if result.occurrences > result.positions.length}<span>…</span>{/if}
  </footer>
</article>

<style>
  .card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 12px;
    display: flex;
    flex-direction: column;
    gap: 10px;
  }
  header {
    display: flex;
    align-items: center;
    gap: 12px;
  }
  .name {
    flex: 1;
    min-width: 0;
  }
  h3 {
    margin: 0;
    font-size: 1rem;
    overflow-wrap: anywhere;
  }
  .name p {
    margin: 2px 0 0;
    color: var(--muted);
    font-size: 0.85rem;
  }
  .name p.embedded {
    color: var(--text);
    font-style: italic;
  }
  .tag {
    display: inline-block;
    margin-left: 4px;
    padding: 0 6px;
    border: 1px solid var(--border);
    border-radius: 4px;
    font-size: 0.75rem;
  }
  .name p .tag:first-child {
    margin-left: 0;
  }
  .play {
    flex: none;
    width: 40px;
    height: 40px;
    border-radius: 50%;
    border: none;
    background: var(--accent);
    color: white;
    font-size: 15px;
    cursor: pointer;
  }
  footer {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 4px 6px;
    color: var(--muted);
    font-size: 0.85rem;
  }
  .time {
    border: 1px solid var(--border);
    background: transparent;
    color: var(--text);
    border-radius: 999px;
    padding: 1px 8px;
    font: inherit;
    font-variant-numeric: tabular-nums;
    cursor: pointer;
  }
  .time.current {
    border-color: var(--accent);
    color: var(--accent);
  }
</style>
