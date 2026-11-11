// Run the shipped worker code on real Three geometry and test UI-thread liveness.
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {Worker} from 'node:worker_threads';
import assert from 'node:assert/strict';
const root=path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const moduleURL='data:text/javascript;base64,'+fs.readFileSync(path.join(root,'assets/vendor/three.module.js')).toString('base64');
const THREE=await import(moduleURL);
const workerCode=fs.readFileSync(path.join(root,'assets/vendor/board-mesh-worker.js'),'utf8').replace("'./three.module.js'",JSON.stringify(moduleURL));
const bootstrap=`import {parentPort} from 'node:worker_threads';
globalThis.self={postMessage:(data,buffers)=>parentPort.postMessage(data,buffers)};
${workerCode}
parentPort.on('message',data=>self.onmessage({data}));`;
const workerURL=new URL('data:text/javascript;base64,'+Buffer.from(bootstrap).toString('base64'));
function calculate(shape,timeout=15000){
  const worker=new Worker(workerURL);
  return new Promise((resolve,reject)=>{
    const timer=setTimeout(()=>{worker.terminate();reject(new Error('board worker timeout'));},timeout);
    worker.once('message',data=>{clearTimeout(timer);worker.terminate();resolve(data);});
    worker.once('error',error=>{clearTimeout(timer);worker.terminate();reject(error);});
    worker.postMessage({shape});
  });
}
const shape=new THREE.Shape();shape.moveTo(0,0);shape.lineTo(40,0);shape.lineTo(40,30);shape.lineTo(0,30);shape.closePath();
for(let x=2;x<39;x+=2)for(let y=2;y<29;y+=2){const hole=new THREE.Path();hole.absarc(x,y,.2,0,Math.PI*2,true);shape.holes.push(hole);}
let ticks=0;const heartbeat=setInterval(()=>ticks++,5);
const result=await calculate(shape.toJSON());clearInterval(heartbeat);
assert(!result.error,result.error);assert(ticks>2,'main thread did not yield');
assert(result.core.attributes.position.array instanceof Float32Array);
assert(result.face.index.length>0);
const positions=result.core.attributes.position.array;
let z0=Infinity,z1=-Infinity;for(let i=2;i<positions.length;i+=3){z0=Math.min(z0,positions[i]);z1=Math.max(z1,positions[i]);}
assert.equal(z0,0);assert.equal(z1,1);
const invalid=await calculate({});assert(invalid.error,'invalid geometry must return a visible error');
// The actual page must use the worker and cache one extrusion for all layers.
const html=fs.readFileSync(path.join(root,'assets/viewer.html'),'utf8');
assert(html.includes('worker.terminate()')&&html.includes('30000'));
assert(!html.includes('new THREE.ExtrudeGeometry(boardShape'));
console.log('PASS: shipped worker handles '+shape.holes.length+' holes, transferable geometry, unit depth, error result and responsive main thread ('+ticks+' ticks)');
