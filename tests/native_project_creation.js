/* Actual home form + native IPC + installed MLT, without media or a model turn. */
(async()=>{
 const report={projects:[],errors:[],navigation:'Batch navigation deferred; each created project is reopened by the runner.'};
 const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
 async function until(predicate){const end=Date.now()+20000;while(Date.now()<end){if(predicate())return;await pause(100);}throw new Error(document.getElementById('notice').textContent||'Project creation timed out');}
 window.addEventListener('error',event=>report.errors.push(event.message));
 try{
  await until(()=>!document.getElementById('project-picker').hidden);
  document.getElementById('quickstart-dialog')?.close();
  let opened=null;
  openProject=async id=>{opened=await api('/api/library/open',{id});opened.id=id;};
  const form=document.getElementById('project-form'),format=form.elements.format,rate=form.elements.fps_num;
  for(const test of [{name:'Automatic default',format:'auto',fps:30},
                     {name:'Manual vertical',format:'vertical',fps:24},
                     {name:'Automatic after manual 60',format:'auto',fps:30,previousRate:'60'}]){
   opened=null;document.getElementById('create-project').click();
   form.elements.name.value=test.name;
   if(test.previousRate){format.value='landscape';format.dispatchEvent(new Event('change'));rate.value=test.previousRate;}
   format.value=test.format;format.dispatchEvent(new Event('change'));
   if(test.format!=='auto')rate.value=String(test.fps);
   if(rate.disabled!==(test.format==='auto'))throw new Error('Unexpected frame-rate enabled state');
   form.querySelector('button[type=submit]').click();
   await until(()=>opened!==null);
   if(!opened.url.startsWith('filmapp://app/?project='+opened.id))throw new Error('Project open URL mismatch');
   report.projects.push({...test,id:opened.id});
   document.getElementById('project-dialog').close();
  }
  report.passed=report.errors.length===0;
 }catch(error){report.passed=false;report.error=error.message;}
 window.webkit.messageHandlers.film.postMessage({id:'smoke',action:'smokeComplete',body:report});
})();
