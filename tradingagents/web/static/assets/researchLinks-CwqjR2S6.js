function t(e){return`/timelines/${encodeURIComponent(e.request.ticker)}?node=${encodeURIComponent(e.id)}${e.trashed_at?"&trash_state=all":""}`}export{t as r};
