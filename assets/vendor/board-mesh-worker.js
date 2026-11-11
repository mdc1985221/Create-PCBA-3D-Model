// PCB triangulation stays off the UI thread; the viewer can terminate bad input.
// Original PCBA3D code; see the project LICENSE.
import {Shape, ExtrudeGeometry, ShapeGeometry} from './three.module.js';

self.onmessage = ({data}) => {
  try {
    const shape = new Shape().fromJSON(data.shape);
    const pack = geometry => {
      const result = {attributes:{}, index:null};
      for (const name of ['position','normal','uv']) {
        const attribute=geometry.getAttribute(name);
        if(attribute)result.attributes[name]={array:attribute.array,itemSize:attribute.itemSize};
      }
      if(geometry.index)result.index=geometry.index.array;
      return result;
    };
    // One unit-depth extrusion serves the core and both soldermask layers.
    const core=pack(new ExtrudeGeometry(shape,{depth:1,bevelEnabled:false,curveSegments:16}));
    const face=pack(new ShapeGeometry(shape,16));
    const buffers=[];
    for(const geometry of [core,face]) {
      for(const attribute of Object.values(geometry.attributes))buffers.push(attribute.array.buffer);
      if(geometry.index)buffers.push(geometry.index.buffer);
    }
    self.postMessage({core,face},buffers);
  } catch(error) {
    self.postMessage({error:String(error.message||error)});
  }
};
