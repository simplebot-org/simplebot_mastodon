window.showReplyForm = (btn) => {
    let form = getParent(btn, "article").getElementsByClassName('reply-form')[0];
    let ta = form.getElementsByTagName("textarea")[0];
    ta.value = ta.getAttribute("mentions");
    form.toggleAttribute("hidden");
};

window.reply = (btn) => {
    let article = getParent(btn, "article");
    let id = article.getAttribute("id");
    let form = article.getElementsByClassName('reply-form')[0];
    let ta = form.getElementsByTagName("textarea")[0];
    if (id && ta.value) {
        sendMsg({text: `/reply ${id} ${ta.value}`}, ta.value);
        form.toggleAttribute("hidden");
    }
};

window.boost = (btn) => {
    let id = getParent(btn, "article").getAttribute("id");
    if (id) {
        btn.setAttribute("onclick", "unboost(this)");
        btn.style.fill = "#2b90d9";
        sendMsg({text: `/boost ${id}`}, `boost ${id}`);
    }
};

window.unboost = (btn) => {
    let id = getParent(btn, "article").getAttribute("id");
    if (id) {
        btn.setAttribute("onclick", "boost(this)");
        btn.style.fill = "";
        sendMsg({text: `/unboost ${id}`}, `unboost ${id}`);
    }
};

window.star = (btn) => {
    let id = getParent(btn, "article").getAttribute("id");
    if (id) {
        btn.setAttribute("onclick", "unstar(this)");
        btn.style.fill = "#2b90d9";
        sendMsg({text: `/star ${id}`}, `star ${id}`);
    }
};

window.unstar = (btn) => {
    let id = getParent(btn, "article").getAttribute("id");
    if (id) {
        btn.setAttribute("onclick", "star(this)");
        btn.style.fill = "";
        sendMsg({text: `/unstar ${id}`}, `unstar ${id}`);
    }
};

window.showOptions = (btn) => {
    document.getElementById('modal').style.display='block';
};

function getParent(element, tagName) {
    let parent = element.parentNode;
    while (parent && tagName && parent.tagName !== tagName.toUpperCase()) {
        parent = parent.parentNode;
    }
    return parent;
}

function h(tag, attributes, ...children) {
    const element = document.createElement(tag);
    if (attributes) {
        Object.entries(attributes).forEach(entry => {
            element.setAttribute(entry[0], entry[1]);
        });
    }
    element.append(...children);
    return element;
}

function getIcon(name, attributes) {
    let element = createElements(`<svg><use xlink:href="/icons.svg#${name}"></use><svg>`)[0];
    if (attributes) {
        Object.entries(attributes).forEach(entry => {
            element.setAttribute(entry[0], entry[1]);
        });
    }    
    return element;
}

function createElements(htmlString) {
    let div = h("div");
    div.innerHTML = htmlString;
    return div.childNodes;
}

function sendMsg(msg, desc) {
    window.webxdc.sendUpdate({payload: {simplebot: msg}}, desc);
}

function main(data) {
    let root = document.getElementById("root");

    if (data.profile) {
        let profile = data.profile;
        let rel = profile.relationships || {};
        let div = (
            h("div", {class: "account-profile-grid"},
              h("div", {class: "account-profile-avatar"},
                getIcon("fa-user", {class: "avatar", style: "width: 80px; height: 80px;"})),
              h("div", {class: "account-profile-name"}, profile.display_name),
              h("div", {class: "account-profile-username"}, `@${profile.username}`),
              h("div", {class: "account-profile-followed-by"}, rel.followed_by? h("span", {class: "account-profile-followed-by-span"}, "Follows you") : ""),
              h("div", {class: "account-profile-follow"},
                getIcon("fa-user-plus", {class: "btn-svg", style: profile.id === data.acct_id? "display: none" : ""})),
              h("div", {class: "account-profile-note"}, ...createElements(profile.note))
             )
        );

        if (profile.fields.length !== 0) {
            let fields = h("div", {class: "account-profile-meta"}, h("div", {class: "account-profile-meta-border"}));
            profile.fields.forEach(field => {
                fields.append(
                    h("div", {class: "account-profile-meta-cell account-profile-meta-name"}, ...createElements(field.name)),
                    h("div", {class: "account-profile-meta-cell account-profile-meta-value"}, ...createElements(field.value)),
                    h("div", {class: "account-profile-meta-cell account-profile-meta-verified"}),
                );
            });
            fields.appendChild(h("div", {class: "account-profile-meta-border"}));
            div.appendChild(fields);
        }

        div.append(
            h("div", {class: "account-profile-details"},
              h("div", {class: "account-profile-details-item"},
                h("span", {class: "account-profile-details-item-title"}, "Toots"),
                h("span", {class: "account-profile-details-item-datum"}, profile.statuses_count)),
              h("div", {class: "account-profile-details-item"},
                h("span", {class: "account-profile-details-item-title"}, "Follows"),
                h("span", {class: "account-profile-details-item-datum"}, profile.following_count)),
              h("div", {class: "account-profile-details-item"},
                h("span", {class: "account-profile-details-item-title"}, "Followers"),
                h("span", {class: "account-profile-details-item-datum"}, profile.followers_count))
             )
        );
        root.appendChild(div);
    }

    data.toots.forEach(toot => {
        let article = h("article", {id: toot.reblog? toot.reblog.id : toot.id, class: "w3-card-2"});

        if (data.notifications) {
            if (toot.type === "reblog") {
                article.append(
                    h("small", {class: "status-header"},
                      getIcon("fa-retweet", {class: "status-header-svg"}),
                      toot.account.display_name, " (@", toot.account.acct, ") boosted your toot")
                );
            } else if (toot.type === "favourite") {
                article.append(
                    h("small", {class: "status-header"},
                      getIcon("fa-star", {class: "status-header-svg"}),
                      toot.account.display_name, " (@", toot.account.acct, ") favorited your toot")
                );
            } else if (toot.type === "follow") {
                article.append(
                    h("div", {},
                      getIcon("fa-user-plus", {class: "status-header-svg"}),
                      toot.account.display_name, " (@", toot.account.acct, ") followed you")
                );
                root.appendChild(article);
                return;
            } else if (toot.type !== "mention") {
                console.log("UNSUPPORTE TYPE: " + toot.type); // TODO: add poll support
                return;
            }
            toot = toot.status;
        } else if (toot.reblog) {
            let small = h(
                "small", {class: "status-header"},
                getIcon("fa-retweet", {class: "status-header-svg"}),
                toot.account.display_name, " (@", toot.account.acct, ") boosted",
                h("br")
            )
            article.appendChild(small);
            toot = toot.reblog;
        }

        article.appendChild(h(
            "a", {class: "status-sidebar"},
            getIcon("fa-user", {class: "avatar", style: "width: 48px; height: 48px;"})
        ));
        article.appendChild(
            h("strong", {}, toot.account.display_name,
              h("small", {}, " (@" + toot.account.acct +")"))
        );

        article.appendChild(h("small", {class: "w3-right"}, toot.created_at.split(" ")[0]));

        let content = h("p");
        if (toot.media_attachments.length) {
            let attachments = h("p");
            toot.media_attachments.forEach(attachment => {
                attachments.append(h("a", {href: attachment.url}, attachment.url), h("br"));
            });
            content.appendChild(attachments);
        }
        content.innerHTML += toot.content;
        article.appendChild(content);

        let toolbar = h(
            "div", {class: "toolbar"}, h(
                "button", {class: "icon-btn", onclick: "showReplyForm(this)"}, getIcon(
                    "fa-reply", {class: "btn-svg"})),
            
        );
        if (toot.visibility === "public" || toot.visibility === "unlisted") {
            let attrs = {
                class: "icon-btn",
                onclick: toot.reblogged? "unboost(this)" : "boost(this)",
                style: toot.reblogged? "fill: #2b90d9" : ""
            };
            toolbar.appendChild(h("button", attrs, getIcon("fa-retweet", {class: "btn-svg"})));
        } else {
            let attrs = {class: "icon-btn", style: "fill: #666"};
            let icon = toot.visibility === "direct"? "fa-envelope" : "fa-lock";
            toolbar.appendChild(h("button", attrs, getIcon(icon, {class: "btn-svg"})));
        }
        
        toolbar.append(
            h("button", {class: "icon-btn", onclick: toot.favourited? "unstar(this)" : "star(this)", style: toot.favourited? "fill: #2b90d9" : ""},
              getIcon("fa-star", {class: "btn-svg"})),
            h("button", {class: "icon-btn", onclick: "showOptions(this)"},
              getIcon("fa-ellipsis-h", {class: "btn-svg"}))
        );
        article.append(toolbar);

        let text = toot.account.id === data.acct_id? "" : `@${toot.account.acct} `;
        toot.mentions.forEach(account => {
            if (account.id !== data.acct_id) {
                text += `@${account.acct} `;
            }
        });
        article.append(
            h("div", {class: "reply-form w3-container", hidden: ""},
              h("strong", {}, data.display_name, h("small", {}, " (@" + data.username +")")),
              h("textarea", {placeholder: "What's on your mind?", mentions: text}),
              h("button", {class: "w3-btn w3-right", onclick: "reply(this)"}, "Toot!"))
        );

        root.appendChild(article);
    });
}

onload = () => {
    fetch("data.json")
        .then(response => response.json())
        .then(json => main(json));
};
