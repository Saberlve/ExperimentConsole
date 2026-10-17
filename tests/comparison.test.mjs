import test from 'node:test';
import assert from 'node:assert/strict';
import {comparePixels, AlignmentGate, STALE_MS} from '../frontend/comparison.mjs';

function pixels(count, value=50) {
  const bytes=new Uint8ClampedArray(count*4);
  for(let i=0;i<bytes.length;i+=4) bytes.set([value,value,value,255],i);
  return bytes;
}

test('identical pixels score 100; color change is rejected and highlighted',()=>{
  const original=pixels(100), changed=pixels(100);
  assert.equal(comparePixels(original,changed).score,100);
  changed.set([255,255,255,255],0);
  assert.equal(comparePixels(original,changed).score,99);
  assert.equal(comparePixels(original,changed).difference[0],255);
  changed.set([255,255,255,255],4);
  assert.equal(comparePixels(original,changed).score,98);
});

test('each RGB channel must be within tolerance; alpha is excluded',()=>{
  const a=new Uint8ClampedArray([50,50,50,255]);
  assert.equal(comparePixels(a,new Uint8ClampedArray([60,40,50,0])).score,100);
  assert.equal(comparePixels(a,new Uint8ClampedArray([61,50,50,255])).score,0);
  assert.throws(()=>comparePixels(a,new Uint8ClampedArray(8)),/尺寸/);
});

test('99 threshold uses unrounded score and three distinct frames',()=>{
  const gate=new AlignmentGate();
  const observe=(score,frameId,now=0,key='camera|reference')=>gate.observe({score,frameId,key,now});
  assert.equal(observe(100,'1').passed,false);
  assert.equal(observe(100,'1',100).count,1);
  assert.equal(observe(99,'2',200).passed,false);
  assert.equal(observe(99,'3',300).passed,true);
  assert.equal(observe(98.999999,'4',400).passed,false);
  assert.equal(observe(100,'5',500).count,1);
});

test('stale frame, camera/reference switch and reset invalidate a pass',()=>{
  const gate=new AlignmentGate();
  for(let i=0;i<3;i++)gate.observe({score:100,frameId:String(i),key:'a|r',now:i*100});
  assert.equal(gate.status(250).passed,true);
  assert.equal(gate.status(200+STALE_MS+1).passed,false);
  assert.equal(gate.observe({score:100,frameId:'3',key:'a|r',now:3000}).count,1);
  assert.equal(gate.observe({score:100,frameId:'4',key:'b|r',now:3100}).count,1);
  assert.equal(gate.observe({score:100,frameId:'5',key:'b|new-ref',now:3200}).count,1);
  gate.reset('相机断流');
  assert.equal(gate.status(3200).passed,false);
  assert.equal(gate.status(3200).score,null);
});
