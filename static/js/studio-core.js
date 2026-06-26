const FoxBotStudio = {

    modules: {},

    register(name, init) {
        this.modules[name] = init;
    },

    start() {

        console.log("🦊 Starting FoxBot Studio");

        Object.values(this.modules).forEach(fn => {

            try {
                fn();
            }
            catch(err){
                console.error(err);
            }

        });

    }

};

document.addEventListener("DOMContentLoaded", ()=>{

    FoxBotStudio.start();

});
