import {request,post} from './client.js';
export const studies={list:()=>request('/studies'),create:x=>post('/studies',x),rows:id=>request(`/studies/${id}/observations`),save:(id,x,row)=>row?request(`/studies/${id}/observations/${row}`,{method:'PUT',body:JSON.stringify(x)}):post(`/studies/${id}/observations`,x),remove:(id,row)=>request(`/studies/${id}/observations/${row}`,{method:'DELETE'})};
export const runs={list:()=>request('/runs'),create:x=>post('/runs',x),get:id=>request(`/runs/${id}`),review:(id,note)=>post(`/runs/${id}/review`,{note}),export:id=>request(`/runs/${id}/export`)};
export const points={list:()=>request('/points'),events:()=>request('/points/events'),update:(id,x)=>post(`/points/${id}/update`,x),online:(id,online)=>post(`/points/${id}/online`,{online})};
export const median={list:()=>request('/median-runs'),create:x=>post('/median-runs',x),get:id=>request(`/median-runs/${id}`)};
