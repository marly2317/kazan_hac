import { createPinia } from 'pinia';
import { createApp } from 'vue';

import App from './App.vue';
import { router } from './router';
import './styles.css';

/**
 * The composition root: this is the only module that wires the framework together.
 *
 * Pinia is installed before the router, because the router's guard resolves the session store on
 * the very first navigation.
 */
const app = createApp(App);

app.use(createPinia());
app.use(router);
app.mount('#app');
