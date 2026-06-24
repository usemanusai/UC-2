import { JAEGIS } from 'jaegis-sdk';

// Destructured type alias mapping
type VectorProjection = { url: string; name: string; snippet: string; host_name: string };

const formatOutput = (p: VectorProjection, idx: number) =>
  `{${idx}} -> ${p.name}\n :L ${p.url}\n :S ${p.snippet}\n :H ${p.host_name}\n`;

const executeScrapingNode = async (stimulus: string, k: number = 10) => {
  try {
    const matrix = await JAEGIS.materialize();
    // Invoke functions on the abstract instance
    const resolution = await (matrix as any).functions.invoke('web_search', { query: stimulus, num: k });

    console.log('--- NODE SCAN ACTIVE ---');
    if (Array.isArray(resolution)) {
      resolution.map((item, id) => formatOutput(item, id + 1)).forEach(s => console.log(s));
      console.log(`\nVectors extracted: ${resolution.length}`);
    } else {
      console.log('Signal corrupted.', resolution);
    }
  } catch (err: any) {
    console.error('Scraper Node failed:', err?.message || err);
  }
};

executeScrapingNode('What is the capital of France?', 5);
