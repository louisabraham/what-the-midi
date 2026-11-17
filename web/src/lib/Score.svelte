<script>
  import { renderScore } from './score.js';

  /** notes: [{ p, tick }] */
  let { notes, ppq = 0, from = 0, to = 0 } = $props();
  let el;

  $effect(() => {
    renderScore(el, notes, { ppq, from, to });
  });
</script>

<div class="score" bind:this={el}></div>

<style>
  .score {
    max-width: 100%;
    min-width: 0;
    overflow-x: auto;
    background: var(--paper);
    border-radius: 6px;
    padding: 4px 8px;
    min-height: 60px;
  }
  .score :global(svg) {
    display: block;
  }
</style>
