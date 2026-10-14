import {expect, test} from "@playwright/test";

test("P6 group filter, explicit bulk preflight, and revision viewer (mock API)", async ({page}) => {
  const account = {id:1,name:"测试账号",phone:"+10000000000",username:"test",user_id:"1",
    avatar:"",status:"online",proxy:"",last_checked:null};
  const makeTask = (id: number, name: string, group: string) => ({
    id, name, group_name:group, account_id:1, kind:"signer", enabled:false,
    cron:"0 8 * * *",timezone:"Asia/Shanghai",delay_min:0,delay_max:0,
    success_pattern:"",failure_pattern:"",response_timeout:20,folder:"",
    config:{chats:[{chat_id:"@TestBot",actions:[{action:1,text:"/checkin"}]}]},
    next_run:null,last_run:null,last_status:"pending",last_summary:"",running:false,
  });
  let tasks = [makeTask(11,"一号任务","媒体组"),makeTask(12,"二号任务","签到组")];
  let batchCalls = 0;
  await page.route("**/api/**", async route => {
    const path = new URL(route.request().url()).pathname;
    const method = route.request().method();
    if(path==="/api/panel/me")return route.fulfill({json:{}});
    if(path==="/api/accounts")return route.fulfill({json:[account]});
    if(path==="/api/tasks" && method==="GET")return route.fulfill({json:tasks});
    if(path==="/api/overview")return route.fulfill({json:{trend:[],recent:[]}});
    if(path==="/api/tasks/batch" && method==="POST"){
      batchCalls++;
      const request = route.request().postDataJSON();
      if(request.dry_run) return route.fulfill({json:{
        dry_run:true,count:request.ids.length,items:request.ids.map((id:number)=>({
          id,name:tasks.find(t=>t.id===id)?.name,
        })),
      }});
      if(request.operation==="disable")tasks=tasks.map(t=>t);
      return route.fulfill({json:{count:request.ids.length,ids:request.ids}});
    }
    if(path==="/api/tasks/11/versions")return route.fulfill({json:{
      task_id:11,current_version:1,current:{name:"一号任务",enabled:false,config:tasks[0].config},
      items:[{version:1,operation:"created",created_at:"2026-10-14T00:00:00Z",
        digest:"abcd",name:"一号任务",group_name:"媒体组",enabled:false}],
    }});
    if(path==="/api/tasks/11/versions/1")return route.fulfill({json:{
      version:1,operation:"created",digest:"abcd",
      snapshot:{name:"一号任务",enabled:false,config:tasks[0].config},
    }});
    return route.fulfill({status:404,json:{detail:"unexpected "+method+" "+path}});
  });
  await page.goto("/");
  await page.locator("nav").getByRole("button",{name:"签到任务",exact:true}).click();
  await expect(page.getByText("一号任务",{exact:true})).toBeVisible();
  await page.getByLabel("筛选管理分组").selectOption("group:媒体组");
  await expect(page.getByText("一号任务",{exact:true})).toBeVisible();
  await expect(page.getByText("二号任务",{exact:true})).toHaveCount(0);
  await page.getByLabel("选择任务 一号任务").check();
  await expect(page.getByText("已选择 1 个")).toBeVisible();
  page.once("dialog", dialog=>dialog.accept());
  await page.getByRole("button",{name:"预览并执行"}).click();
  await expect.poll(()=>batchCalls).toBe(2);
  await page.getByRole("button",{name:"更多任务操作"}).first().click();
  await page.getByRole("button",{name:"配置版本 / 回滚"}).click();
  await expect(page.getByText("配置版本 · 一号任务")).toBeVisible();
  await expect(page.getByText("当前版本 V1")).toBeVisible();
});
