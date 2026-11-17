<script>
  import * as api from './lib/api.js';
  import { midiToName } from './lib/notes.js';
  import Piano from './lib/Piano.svelte';
  import { playNotes } from './lib/player.js';
  import Result from './lib/Result.svelte';
  import Score from './lib/Score.svelte';

  const EXAMPLES = [
    { label: 'Für Elise', q: 'E5 D#5 E5 D#5 E5 B4 D5 C5 A4', transpose: false },
    { label: 'Ode to Joy (any key)', q: 'E4 E4 F4 G4 G4 F4 E4 D4', transpose: true },
  ];

  const url = new URLSearchParams(location.search);
  let query = $state(url.get('q') ?? '');
  let octave = $state(url.get('octave') === '1');
  let transpose = $state(url.get('transpose') === '1');
  let pianoOctave = $state(4);

  let data = $state(null);
  let error = $state(null);
  let loading = $state(false);
  let info = $state(null);
  let requestId = 0;

  api.stats().then((s) => (info = s)).catch(() => {});

  async function run(q, octave, transpose) {
    const params = new URLSearchParams();
    if (q.trim()) params.set('q', q.trim());
    if (octave) params.set('octave', '1');
    if (transpose) params.set('transpose', '1');
    history.replaceState(null, '', params.size ? `?${params}` : location.pathname);

    const id = ++requestId;
    if (!q.trim()) {
      data = error = null;
      return;
    }
    loading = true;
    try {
      const result = await api.search({ q, octave, transpose });
      if (id !== requestId) return;
      data = result;
      error = null;
    } catch (e) {
      if (id !== requestId) return;
      error = e;
    } finally {
      if (id === requestId) loading = false;
    }
  }

  // search 250 ms after the last change
  $effect(() => {
    const args = [query, octave, transpose];
    const timer = setTimeout(() => run(...args), 250);
    return () => clearTimeout(timer);
  });

  function addNote(midi) {
    query = `${query.trim()} ${midiToName(midi)}`.trim();
  }

  function removeNote() {
    query = query.trim().split(/[\s,;]+/).slice(0, -1).join(' ');
  }

  function example(e) {
    query = e.q;
    transpose = e.transpose;
    octave = false;
  }

  let queryNotes = $derived(data?.query.map((n) => ({ p: n.midi, tick: 0 })) ?? []);
  const fmt = (n) => n.toLocaleString('en');
</script>

<main>
  <header class="top">
    <h1>What the MIDI</h1>
    <p>
      Search a melody in
      {#if info}{fmt(info.files)} MIDI files ({fmt(info.notes)} notes){:else}a MIDI collection{/if}.
    </p>
  </header>

  <section class="search">
    <form onsubmit={(e) => { e.preventDefault(); run(query, octave, transpose); }}>
      <label for="q">Notes</label>
      <div class="row">
        <input id="q" type="text" bind:value={query} autocomplete="off" spellcheck="false"
          placeholder="E5 D#5 E5 D#5 E5 B4 D5 C5 A4" aria-invalid={!!error}
          aria-describedby="help" />
        <button type="button" onclick={removeNote} disabled={!query} aria-label="Remove the last note">⌫</button>
        <button type="button" onclick={() => (query = '')} disabled={!query}>Clear</button>
      </div>
      <p id="help" class="help">
        Note names (middle C = C4, sharps C#4, flats Bb3) or MIDI numbers (60).
        Without an octave number, the note closest to the previous one.
      </p>
    </form>

    <Piano onnote={addNote} bind:octave={pianoOctave} />

    <div class="options">
      <label><input type="checkbox" bind:checked={octave} /> Ignore octave</label>
      <label><input type="checkbox" bind:checked={transpose} /> Any key (transposition)</label>
      <span class="examples">
        Examples:
        {#each EXAMPLES as e (e.label)}
          <button type="button" class="link" onclick={() => example(e)}>{e.label}</button>
        {/each}
      </span>
    </div>

    {#if error}
      <p class="error" role="alert">{error.message}</p>
    {:else if queryNotes.length}
      <div class="query">
        <Score notes={queryNotes} />
        <button type="button" onclick={() => playNotes(queryNotes.map((n, i) => ({ p: n.p, sec: i * 0.35 })))}>
          ▶ Play the query
        </button>
      </div>
    {/if}
  </section>

  {#if data && !error}
    <section class="results" aria-busy={loading}>
      <p class="summary">
        {#if data.total}
          {fmt(data.total)} {data.total === 1 ? 'match' : 'matches'} in
          {fmt(data.files_total)}{data.files_exact ? '' : '+'} {data.files_total === 1 ? 'file' : 'files'}
          · {data.elapsed_ms} ms
          {#if data.files_total > data.results.length}· first {data.results.length} files{/if}
          {#if data.files_approximate}· {fmt(data.files_approximate)} approximate{/if}
        {:else if data.files_approximate}
          No exact match · {fmt(data.files_approximate)} approximate {data.files_approximate === 1 ? 'match' : 'matches'}
          (extra notes between your notes, or notes not found)
        {:else}
          No match. Try fewer notes, “Ignore octave”, or “Any key”.
        {/if}
      </p>
      {#each data.results as result (result.doc)}
        <Result {result} />
      {/each}
    </section>
  {/if}
</main>

<style>
  main {
    max-width: 860px;
    margin: 0 auto;
    padding: 24px 16px 64px;
    display: flex;
    flex-direction: column;
    gap: 20px;
  }
  .top h1 {
    margin: 0;
    font-size: 1.8rem;
  }
  .top p {
    margin: 4px 0 0;
    color: var(--muted);
  }
  .search {
    display: flex;
    flex-direction: column;
    gap: 12px;
  }
  label[for='q'] {
    font-weight: 600;
  }
  .row {
    display: flex;
    gap: 6px;
    margin-top: 6px;
  }
  input[type='text'] {
    flex: 1;
    min-width: 0;
    font: inherit;
    font-size: 1.05rem;
    padding: 8px 10px;
    border: 1px solid var(--border);
    border-radius: 6px;
    background: var(--surface);
    color: var(--text);
  }
  input[aria-invalid='true'] {
    border-color: var(--error);
  }
  .row button,
  .query button {
    font: inherit;
    padding: 0 12px;
    border: 1px solid var(--border);
    border-radius: 6px;
    background: var(--surface);
    color: var(--text);
    cursor: pointer;
  }
  .row button:disabled {
    opacity: 0.4;
    cursor: default;
  }
  .help {
    margin: 6px 0 0;
    font-size: 0.85rem;
    color: var(--muted);
  }
  .options {
    display: flex;
    flex-wrap: wrap;
    gap: 8px 20px;
    align-items: center;
  }
  .examples {
    color: var(--muted);
    font-size: 0.9rem;
  }
  .link {
    background: none;
    border: none;
    padding: 0 4px;
    color: var(--accent);
    font: inherit;
    text-decoration: underline;
    cursor: pointer;
  }
  .error {
    margin: 0;
    color: var(--error);
  }
  .query {
    display: flex;
    flex-direction: column;
    gap: 6px;
    align-items: flex-start;
  }
  .query :global(.score) {
    max-width: 100%;
  }
  .query button {
    padding: 6px 12px;
  }
  .results {
    display: flex;
    flex-direction: column;
    gap: 12px;
  }
  .results[aria-busy='true'] {
    opacity: 0.6;
  }
  .summary {
    margin: 0;
    color: var(--muted);
  }
</style>
